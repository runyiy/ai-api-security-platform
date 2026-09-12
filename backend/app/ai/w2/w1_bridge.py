"""Typed, call-scoped W1 fake extensions. No native transport implementation."""
from contextlib import contextmanager
from dataclasses import replace
from hashlib import sha256
from uuid import uuid4

from app.ai.proposals.adapter import ProposalOutcome, Usage, ZERO_USAGE
from app.ai.proposals.bindings import AuthorizationSnapshot, ReservationReceipt, PERMISSIONS
from app.ai.proposals.codec import ProposalRejected
from .records import make, require, PortError, BRIDGE, stamp, utc, usage_view


def bridge(function):
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except PortError as error:
            raise ProposalRejected(BRIDGE[error.code]) from None
    return wrapped


class MemoryPermitPort:
    """The only W2 extension accepted by concrete MemoryWire qualification."""
    def __init__(self, body_digest, consume, close, remaining):
        self.body_digest, self.consume, self.close_stream, self.remaining = body_digest, consume, close, remaining

    @bridge
    def write(self, request, timeout):
        require(type(request) is bytes and len(request) <= 45056, 'LIMIT_EXCEEDED')
        _, separator, body = request.partition(b'\r\n\r\n')
        require(separator and len(body) <= 32768 and sha256(body).hexdigest() == self.body_digest, 'CONTEXT_CHANGED')
        # Only the non-secret synthetic body crosses the endpoint IPC channel.
        return self.consume(body, min(timeout, self.remaining(timeout)))

    @bridge
    def close(self):
        return self.close_stream()


class TerminalObservationPort:
    def __init__(self, record):
        self.record = record

    @bridge
    def observe(self, terminal, usage):
        require(terminal in ('NONE', 'COMPLETED', 'INCOMPLETE', 'FAILED', 'CANCELLED', 'UNKNOWN'))
        self.record(terminal, usage_view(usage))


class CallClock:
    def __init__(self, deadline):
        self.deadline = deadline

    @bridge
    def monotonic(self):
        self.deadline.check()
        return self.deadline.last_ns/1e9

    @bridge
    def utcnow(self):
        return self.deadline.check()

    @bridge
    def remaining(self, maximum):
        return self.deadline.remaining(maximum)


class CallRuntime:
    def __init__(self, prepared, run, deadline, preparation, store, observer, guard):
        self.prepared, self.run, self.deadline = prepared, run, deadline
        self.preparation, self.store, self.observer, self.guard = preparation, store, observer, guard
        self.key, self.reservation, self.admission, self.permit = prepared.key, None, None, None
        self.guard_token, self._send_manager = None, None
        self.clock = CallClock(deadline)
        self.terminal = None
        self.closed = False
        self.receipt = None
        self.hook = lambda stage: None

    def reserve_v1(self):
        self.preparation.requalify(self.prepared, self.deadline)
        try:
            self.reservation = self.store.reserve_v1(self.run, self.key, self.prepared, self.deadline)
            self.observer.register_reserved(self.prepared, self.run, self.reservation)
        except Exception as error:
            if isinstance(error,PortError) and error.code in ('RESERVATION_UNAVAILABLE','OWNER_LOST','CONFLICT') and self.reservation is None:
                raise
            unresolved=True
            try:
                self.store.mark_unknown(self.run,self.key)
            except PortError as missing:
                unresolved=missing.code!='RESERVATION_UNAVAILABLE'
            except Exception:
                pass
            if unresolved:
                try:self.observer.suspend()
                except Exception:pass
            raise
        core, config = self.prepared.core, self.prepared.config
        self.receipt = ReservationReceipt(self.key.call_ref, core.w1_binding_digest, config.account_ref,
            config.revision, utc(core.expires_at), 'synthetic', 4096, 1024,
            core.reserved_tokens, core.reserved_microusd, 'USD', core.rate_card, core.usage_mapping)
        return self.reservation

    def _receipt(self, receipt):
        require(self.receipt is not None and receipt == self.receipt, 'RESERVATION_UNAVAILABLE')

    def _current_snapshot(self, project_ref, context_ref, stage):
        require((project_ref, context_ref) == (self.key.scope.project_ref, self.key.scope.context_ref), 'SOURCE_UNAVAILABLE')
        from .records import STAGES
        require(stage in STAGES)
        self.hook(stage)
        self.deadline.check()
        if stage in ('after_send', 'before_consume', 'before_return', 'final_return', 'complete'):
            require(self.observer.available and self.observer.coverage() and self.observer.witness.accepted[self.key.fingerprint()] == 1,
                    'OBSERVER_UNAVAILABLE')
        view = self.preparation.requalify(self.prepared, self.deadline, token=self.guard_token)
        with self.store.transaction(self.deadline) as db:
            balances = self.store._balances(db, self.key.scope)
            row, _ = self.store._call(db, self.key, self.run)
            self.observed_cancel_generation = balances[1][0]['cancel_generation']
            require(not row['cancelled'] and all(b['state'] != 'CANCELLED' for b, _ in balances), 'CANCELLED')
            require(stage in ('before_consume', 'before_return', 'final_return', 'complete') or
                    all(b['state'] == 'ACTIVE' for b, _ in balances), 'RESERVATION_UNAVAILABLE')
        self.deadline.check((view.valid_from, view.expires_at))
        self.hook('qualified_' + stage)
        self.deadline.check((view.valid_from, view.expires_at))
        if stage in ('after_send','before_consume','before_return','final_return','complete'):
            self.observer.qualify_consumption(self.permit,self.deadline.remaining(3))
            self.deadline.check((view.valid_from,view.expires_at))
        return AuthorizationSnapshot(self.prepared.prepared.registry, self.prepared.config, PERMISSIONS,
                                     utc(view.valid_from), utc(view.expires_at), 'active')

    def read_authority_v1(self,request):
        require(request._name=='AuthorityRead' and request.run==self.run and request.key==self.key
            and request.expected_w1_digest==self.prepared.core.w1_binding_digest
            and request.expected_config_revision==self.prepared.config.revision,'CONTEXT_CHANGED')
        snapshot=self._current_snapshot(self.key.scope.project_ref,self.key.scope.context_ref,request.stage)
        from .ipc import snapshot_document
        from app.ai.proposals.codec import canonical
        digest=sha256(canonical(snapshot_document(snapshot))).hexdigest()
        ref='snapshot_'+digest[:32]
        view=make('AuthorityView',key=self.key,owner_generation=self.run.owner_generation,snapshot_ref=ref,
            snapshot_digest=digest,valid_from=stamp(snapshot.valid_from),expires_at=stamp(snapshot.expires_at),
            observed_cancel_generation=self.observed_cancel_generation)
        self.authority_snapshot=(view,snapshot)
        self.deadline.check((view.valid_from,view.expires_at))
        return view

    @bridge
    def current(self,project_ref,context_ref,stage):
        require((project_ref,context_ref)==(self.key.scope.project_ref,self.key.scope.context_ref),'SOURCE_UNAVAILABLE')
        request=make('AuthorityRead',run=self.run,key=self.key,stage=stage,
            expected_w1_digest=self.prepared.core.w1_binding_digest,expected_config_revision=self.prepared.config.revision)
        view=self.read_authority_v1(request)
        require(self.authority_snapshot[0]==view,'CONTEXT_CHANGED')
        self.deadline.check((view.valid_from,view.expires_at))
        return self.authority_snapshot[1]

    @bridge
    def admit(self, receipt, binding_digest, timeout):
        self._receipt(receipt)
        require(binding_digest == self.prepared.core.w1_binding_digest, 'CONFLICT')
        self.deadline.remaining(timeout)
        self.observer.qualify_admission(self.deadline.remaining(timeout))
        self.deadline.check()
        self.admission = self.store.admit_v1(self.run, self.reservation, self.deadline)
        self.observer.register_call(self.prepared, self.run, self.admission, self.reservation)
        self.hook('admitted')
        self.deadline.check()

    def begin_send(self):
        require(self.admission is not None and self.permit is None, 'RESERVATION_UNAVAILABLE')
        self._send_manager = self.guard.read(self.deadline)
        self.guard_token = self._send_manager.__enter__()
        try:
            ticket = self.observer.begin_acceptance_v1(self.run, self.admission,
                           self.prepared.core.body_digest, self.deadline.remaining())
            self.deadline.check()
            self.preparation.requalify(self.prepared, self.deadline, token=self.guard_token)
            marker = self.store.marker(self.run, self.admission, ticket, self.deadline)
            self.hook('marker_ack')
            self.guard_token.verify(self.deadline)
            self.permit = self.observer.issue(self.run, self.admission, ticket, marker,
                                              self.guard_token.epoch, self.deadline.remaining())
            self.deadline.check()
            self.hook('permit_ack')
            return self.permit
        except BaseException:
            self.end_send()
            raise

    def end_send(self):
        if self._send_manager is not None:
            manager, self._send_manager = self._send_manager, None
            self.guard_token = None
            manager.__exit__(None, None, None)

    @contextmanager
    def send_guard_v1(self,run,admission):
        require(run==self.run and admission==self.admission,'OWNER_LOST')
        try:
            yield self.begin_send()
        finally:
            try:
                self.end_send()
            finally:
                self.close()

    @contextmanager
    def sending(self, receipt, check):
        try:
            self._receipt(receipt)
            with self.send_guard_v1(self.run,self.admission):
                check()
                yield
        except PortError as error:
            raise ProposalRejected(BRIDGE[error.code]) from None

    def consume(self, body, timeout):
        require(self.permit is not None, 'OBSERVER_UNAVAILABLE')
        self.deadline.check()
        result = self.observer.consume_write_v1(self.permit, body, self.deadline.remaining(timeout))
        self.deadline.check()
        return result

    def close(self):
        if self.key.fingerprint() in self.observer.calls:
            self.observer.close_call(self.key, 3)
        self.closed = True

    def observe_terminal(self, terminal, usage):
        # This projection is additional evidence; endpoint final usage remains
        # independent and survives deliberate omission of this callback.
        self.terminal = terminal, usage
        self.store.terminal_projection(self.run,self.key,terminal,usage)
        self.hook('terminal_observation')

    def reconcile(self):
        events = self.observer.events(self.key)
        result = None
        for offset in range(0, len(events), 8):
            try:
                result = self.store.reconcile_v1(self.run, self.key, [e.event_id for e in events[offset:offset+8]], self.observer)
            except PortError as error:
                if error.code!='OBSERVER_UNAVAILABLE':raise
                self.store.mark_unknown(self.run,self.key)
                return self.store.current_settlement(self.key)
        if not events:
            self.store.mark_unknown(self.run, self.key)
        return result

    @bridge
    def record(self, receipt, outcome):
        self._receipt(receipt)
        require(not outcome.display and outcome.refusal_code is None)
        self.hook('before_record')
        self.reconcile()

    @bridge
    def finish(self, receipt):
        self._receipt(receipt)
        self.close()
        self.reconcile()
        self.store.finish(self.run, self.key, self.observer)

    def complete_v1(self, outcome):
        # Accounting may finish after deadline/cancellation. Display may not.
        settlement = None
        try:
            self.close()
            settlement = self.reconcile()
            self.store.finish(self.run, self.key, self.observer)
        except Exception:
            self.store.mark_unknown(self.run, self.key)
            outcome = replace(outcome, code='AUDIT_UNAVAILABLE', display=(), refusal_code=None)
        if outcome.display or outcome.refusal_code is not None:
            observed=self.observer.observations.get(settlement.final_event_id) if settlement is not None else None
            if (settlement is None or settlement.state!='KNOWN' or observed is None
                    or observed.usage!=usage_view(outcome.usage)):
                outcome=replace(outcome,code='AUDIT_UNAVAILABLE',display=(),refusal_code=None)
        at = self.deadline.clock.utcnow()
        completion = make('Completion', key_digest=self.key.fingerprint(), completion_id=uuid4().hex,
            outcome_code=outcome.code, delivery=outcome.delivery, usage=usage_view(outcome.usage),
            settlement_ref=settlement.settlement_id if settlement else None, display_state='SUPPRESSED',
            evaluated_at=stamp(at), expires_at=self.prepared.core.expires_at)
        self.store.persist_completion(self.run, self.key, completion)
        if outcome.code is None or outcome.refusal_code is not None:
            try:
                self.current(self.key.scope.project_ref, self.key.scope.context_ref, 'complete')
                self.deadline.check()
                completion = make('Completion', **{**{k: v for k, v in completion.items() if k != 'format'},
                                                  'display_state': 'ELIGIBLE_NOW', 'evaluated_at': stamp(self.deadline.last_wall)})
            except ProposalRejected as error:
                outcome = replace(outcome, code=error.code, display=(), refusal_code=None)
        else:
            outcome = replace(outcome, display=(), refusal_code=None)
        return completion, outcome
