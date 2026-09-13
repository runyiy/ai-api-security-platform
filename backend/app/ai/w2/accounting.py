"""Atomic dual-scope reservations and conservative, append-only settlement."""
from contextlib import contextmanager
from hashlib import sha256
from time import monotonic
from uuid import uuid4

from sqlalchemy import select, insert, update, func, text, and_
from sqlalchemy.exc import IntegrityError
from app.ai.proposals.bindings import RATE_CARD, USAGE_MAPPING
from app.ai.proposals.codec import canonical, bounded_json
from . import schema as t
from .records import make, decode, require, PortError, MAX_N, stamp, utc, _scalar


def scope_document(scope, kind):
    fields = ('account_ref', 'deployment_ref', 'policy_id', 'policy_version')
    if kind == 'task':
        fields += ('task_id', 'project_ref', 'context_ref', 'context_version', 'context_generation')
    return {k: scope[k] for k in fields}


def balance_id(scope, kind):
    return sha256(canonical({'kind': kind, **scope_document(scope, kind)})).hexdigest()


def actual(usage):
    require(usage.state == 'known', 'COMMIT_UNKNOWN')
    # Integer tenths avoid binary floating point and round up once per call.
    i, o, c, w = (usage[k] for k in ('input_tokens', 'output_tokens', 'cached_input_tokens', 'cache_write_tokens'))
    cost = (20*(i-c-w) + 2*c + 25*w + 120*o + 9)//10
    require(cost <= MAX_N, 'LIMIT_EXCEEDED')
    return usage.total_tokens, cost


class BudgetStore:
    def __init__(self, sessions, writer):
        self.sessions, self.writer = sessions, writer
        self.hook = lambda stage: None  # Parent-controlled fault schedule only.

    @contextmanager
    def transaction(self, deadline=None):
        with self.sessions() as db:
            with db.begin():
                if deadline is None:
                    db.execute(text("SET LOCAL statement_timeout='3s'"))
                    db.execute(text("SET LOCAL lock_timeout='3s'"))
                else:
                    deadline.sql_timeout(db)
                require(db.scalar(text('SHOW synchronous_commit')) == 'on', 'AUTHORITY_UNAVAILABLE')
                yield db
            self.hook('after_commit')
        if deadline is not None:
            deadline.check()

    def install_policy(self, scope, kind, caps, manifest_digest, decision_ref, valid_from, expires_at, deadline):
        require(kind in ('task', 'account') and type(caps) is tuple and len(caps) == 4)
        require(all(v is None or _scalar('N', v) for v in caps))
        require(_scalar('Hash', manifest_digest) and _scalar('Id', decision_ref))
        bid = balance_id(scope, kind)
        body = dict(balance_id=bid, scope_kind=kind, account_ref=scope.account_ref,
            task_id=scope.task_id if kind == 'task' else '', policy_id=scope.policy_id, policy_version=scope.policy_version,
            scope=scope_document(scope, kind), manifest_digest=manifest_digest, decision_ref=decision_ref,
            rate_card=RATE_CARD, currency='USD', token_cap=caps[0], cost_cap=caps[1], call_cap=caps[2], wall_cap_ns=caps[3],
            valid_from=utc(valid_from), expires_at=utc(expires_at))
        digest = sha256(canonical({**body, 'valid_from': valid_from, 'expires_at': expires_at})).hexdigest()
        with self.writer.mutation('CONFIGURATION', decision_ref, digest, deadline) as ticket:
            with self.transaction(deadline) as db:
                ticket.bind(db)
                # Serializes initial account-row creation as well as new periods.
                db.execute(text('SELECT pg_advisory_xact_lock(73105, 1)'))
                old = db.execute(select(t.policy).where(t.policy.c.balance_id == bid)).mappings().one_or_none()
                if old is not None:
                    require(dict(old) == body, 'CONFLICT')
                else:
                    prior = list(db.execute(select(t.balance).join(t.policy)
                        .where(t.policy.c.account_ref == scope.account_ref, t.policy.c.scope_kind == 'account')
                        .with_for_update(of=t.balance)).mappings())
                    require(all(p['held_tokens'] == p['held_microusd'] == 0 and p['state'] not in ('PAUSED_UNKNOWN', 'CANCELLED')
                                for p in prior), 'RESERVATION_UNAVAILABLE')
                    if kind == 'task':
                        require(db.scalar(select(func.count()).select_from(t.policy).where(
                            t.policy.c.account_ref == scope.account_ref, t.policy.c.scope_kind == 'task')) < 4096, 'LIMIT_EXCEEDED')
                    db.execute(insert(t.policy).values(**body))
                    db.execute(insert(t.balance).values(balance_id=bid, settled_tokens=0, settled_microusd=0,
                        held_tokens=0, held_microusd=0, calls=0, wall_ns=0, state='ACTIVE', cancel_generation=1))
            ticket.committed()
        return bid

    def _balances(self, db, scope, *, active=False, at=None, core=None):
        rows = []
        for kind in ('account', 'task'):
            bid = balance_id(scope, kind)
            balance = db.execute(select(t.balance).where(t.balance.c.balance_id == bid).with_for_update()).mappings().one_or_none()
            policy = db.execute(select(t.policy).where(t.policy.c.balance_id == bid)).mappings().one_or_none()
            require(balance is not None and policy is not None and policy['scope'] == scope_document(scope, kind), 'RESERVATION_UNAVAILABLE')
            if active:
                require(balance['state'] == 'ACTIVE' and policy['valid_from'] <= at < policy['expires_at'], 'RESERVATION_UNAVAILABLE')
                require(policy['currency'] == 'USD' and policy['rate_card'] == RATE_CARD, 'RESERVATION_UNAVAILABLE')
                require(all(policy[k] is not None for k in ('token_cap', 'cost_cap', 'call_cap', 'wall_cap_ns')), 'RESERVATION_UNAVAILABLE')
                require(balance['settled_tokens'] + balance['held_tokens'] <= policy['token_cap']
                    and balance['settled_microusd'] + balance['held_microusd'] <= policy['cost_cap'], 'RESERVATION_UNAVAILABLE')
                if core:
                    require(utc(core.expires_at) <= policy['expires_at'], 'RESERVATION_UNAVAILABLE')
            rows.append((dict(balance), dict(policy)))
        return rows

    def _call(self, db, key, ctx=None):
        require(key._name == 'ReservationKey')
        core = db.execute(select(t.core).where(t.core.c.key_digest == key.fingerprint())).mappings().one_or_none()
        require(core is not None and bytes(core['key_bytes']) == key.encode(), 'RESERVATION_UNAVAILABLE')
        record = decode('BindingCore', bytes(core['core_bytes']))
        require(record.fingerprint() == key.core_digest and record.scope == key.scope
                and record.call_ref == key.call_ref, 'CONFLICT')
        row = db.execute(select(t.reservation).where(t.reservation.c.key_digest == key.fingerprint())
                         .with_for_update()).mappings().one_or_none()
        require(row is not None, 'RESERVATION_UNAVAILABLE')
        if ctx is not None:
            require(row['owner_id'] == ctx.owner_id and row['owner_generation'] == ctx.owner_generation, 'OWNER_LOST')
            scoped = ctx.read.scope if ctx._name == 'RecoveryContext' else ctx.scope
            require(scoped == key.scope, 'OWNER_LOST')
        return dict(row), record

    def _event(self, db, row, kind, record, event_id=None, *, recovery=False):
        raw = record.encode() if hasattr(record, 'encode') else canonical(record)
        require(len(raw) <= 4096, 'LIMIT_EXCEEDED')
        digest = sha256(raw).hexdigest()
        eid = event_id or uuid4().hex
        prior = db.execute(select(t.event).where(t.event.c.event_id == eid)).mappings().one_or_none()
        if prior is not None:
            require(prior['key_digest'] == row['key_digest'] and bytes(prior['record']) == raw, 'CONFLICT')
            return eid
        require(row['event_count'] < (64 if recovery else 56), 'LIMIT_EXCEEDED')
        row['event_count'] += 1
        db.execute(insert(t.event).values(event_id=eid, key_digest=row['key_digest'], sequence=row['event_count'],
                                        kind=kind, digest=digest, record=raw))
        db.execute(update(t.reservation).where(t.reservation.c.key_digest == row['key_digest']).values(event_count=row['event_count']))
        return eid

    def _pause(self, db, key, row, balances, *, state='IN_DOUBT'):
        # The locked dictionaries are the transaction's working projections.
        # Updating SQL alone lets a later event overwrite the pause with stale
        # ACTIVE/SETTLED values. Cleanup must never downgrade a conflict either.
        row['state'] = 'CONFLICT' if row['state'] == 'CONFLICT' else state
        db.execute(update(t.reservation).where(t.reservation.c.key_digest == key.fingerprint()).values(state=row['state']))
        for balance, _ in balances:
            balance['state'] = 'CANCELLED' if balance['state'] == 'CANCELLED' else 'PAUSED_UNKNOWN'
            db.execute(update(t.balance).where(t.balance.c.balance_id == balance['balance_id']).values(
                state=balance['state']))

    def _retain(self, db, key, row, balances, incoming=None):
        receipt = decode('Reservation', bytes(row['receipt']))
        held = [max(row['held_tokens'], receipt.reserved_tokens),
                max(row['held_microusd'], receipt.reserved_microusd)]
        if incoming is not None and incoming.usage.state == 'known':
            try:
                tokens, cost = actual(incoming.usage)
                held = [max(held[0], tokens-row['actual_tokens']),
                        max(held[1], cost-row['actual_microusd'])]
            except PortError:
                # Exact counts remain immutable evidence. Unrepresentable cost
                # is a coverage failure, never a truncated final amount.
                pass
        for index, dimension in enumerate(('tokens', 'microusd')):
            field = 'held_'+dimension
            if any(b[field]+held[index]-row[field] > MAX_N for b, _ in balances):
                # Retain the representable original reserve where possible;
                # the frozen scope and evidence carry the unbounded remainder.
                original = max(row[field], receipt['reserved_'+dimension])
                held[index] = original if all(b[field]+original-row[field] <= MAX_N for b, _ in balances) else row[field]
        for balance, _ in balances:
            for dimension, amount in zip(('tokens', 'microusd'), held):
                field = 'held_'+dimension
                balance[field] += amount-row[field]
            db.execute(update(t.balance).where(t.balance.c.balance_id == balance['balance_id']).values(
                held_tokens=balance['held_tokens'], held_microusd=balance['held_microusd']))
        row.update(held_tokens=held[0], held_microusd=held[1])
        db.execute(update(t.reservation).where(t.reservation.c.key_digest == key.fingerprint()).values(
            held_tokens=held[0], held_microusd=held[1]))

    def reserve_v1(self, run, key, prepared, deadline):
        require(prepared.key == key and run.scope == key.scope and run.call_ref == key.call_ref
                and run.attempt == 1, 'RESERVATION_UNAVAILABLE')
        core = prepared.core
        deadline.check((core.valid_from, core.expires_at))
        require(utc(run.deadline_at) <= utc(core.expires_at), 'DEADLINE_EXCEEDED')
        with self.transaction(deadline) as db:
            balances = self._balances(db, key.scope, active=True, at=deadline.check(), core=core)
            prior = db.execute(select(t.core.c.key_digest).where(t.core.c.key_digest == key.fingerprint())).scalar_one_or_none()
            if prior is not None:
                row, old = self._call(db, key, run)
                require(old == core, 'CONFLICT')
                return decode('Reservation', bytes(row['receipt']))
            existing = db.scalar(select(t.core.c.key_bytes).where(t.core.c.task_id==key.scope.task_id,
                t.core.c.case_id==key.scope.case_id,t.core.c.question_id==core.question_id))
            conflicted = existing is not None
            if conflicted:
                original=decode('ReservationKey',bytes(existing))
                require(original.scope==key.scope,'RESERVATION_UNAVAILABLE')
                row,prior_core=self._call(db,original)
                self._conflict(db,original,row,balances,prior_core.fingerprint(),core.fingerprint(),'CORE_CONFLICT')
            else:
                for balance, policy in balances:
                    if policy['scope_kind'] == 'task':
                        require(policy['manifest_digest'] == prepared.manifest_digest, 'RESERVATION_UNAVAILABLE')
                    require(balance['settled_tokens'] + balance['held_tokens'] + core.reserved_tokens <= policy['token_cap']
                        and balance['settled_microusd'] + balance['held_microusd'] + core.reserved_microusd <= policy['cost_cap']
                        and balance['calls'] < policy['call_cap'] and balance['wall_ns']+30_000_000_000 <= policy['wall_cap_ns'], 'RESERVATION_UNAVAILABLE')
                cases = list(db.execute(select(t.core.c.key_digest, t.reservation.c.state).join(t.reservation)
                    .where(t.core.c.task_id == key.scope.task_id, t.core.c.case_id == key.scope.case_id)).all())
                require(len(cases) < 2 and all(c.state == 'SETTLED' for c in cases), 'RESERVATION_UNAVAILABLE')
                require(db.scalar(select(func.count(func.distinct(t.core.c.case_id))).where(t.core.c.task_id == key.scope.task_id)) < 32
                        or bool(cases), 'LIMIT_EXCEEDED')
                receipt = make('Reservation', key=key, reservation_id=uuid4().hex, owner_id=run.owner_id,
                    owner_generation=run.owner_generation, input_limit=4096, output_limit=1024,
                    reserved_tokens=core.reserved_tokens, reserved_microusd=core.reserved_microusd,
                    currency='USD', rate_card=RATE_CARD, usage_mapping=USAGE_MAPPING, expires_at=core.expires_at, measurement_kind='synthetic')
                db.execute(insert(t.core).values(key_digest=key.fingerprint(), key_bytes=key.encode(), core_bytes=core.encode(),
                    task_id=key.scope.task_id, case_id=key.scope.case_id, question_id=core.question_id, call_ref=key.call_ref,
                    account_ref=key.scope.account_ref, deployment_ref=key.scope.deployment_ref,
                    account_balance=balance_id(key.scope, 'account'), task_balance=balance_id(key.scope, 'task')))
                row = dict(key_digest=key.fingerprint(), reservation_id=receipt.reservation_id, receipt=receipt.encode(),
                    owner_id=run.owner_id, owner_generation=run.owner_generation, state='RESERVED', held_tokens=core.reserved_tokens,
                    held_microusd=core.reserved_microusd, actual_tokens=0, actual_microusd=0, event_count=0, closed=False, cancelled=False, revision=0)
                db.execute(insert(t.reservation).values(**row))
                self._event(db, row, 'RESERVED', dict(key_digest=key.fingerprint(), core_digest=key.core_digest))
                for balance, _ in balances:
                    db.execute(update(t.balance).where(t.balance.c.balance_id == balance['balance_id']).values(
                        held_tokens=balance['held_tokens']+core.reserved_tokens, held_microusd=balance['held_microusd']+core.reserved_microusd,
                        calls=balance['calls']+1, wall_ns=balance['wall_ns']+30_000_000_000))
                self.hook('before_reserve_commit')
        if conflicted:
            raise PortError('CONFLICT',key.fingerprint())
        return receipt

    def lookup_reservation_v1(self, read, key, deadline):
        require(read.scope == key.scope, 'RESERVATION_UNAVAILABLE')
        with self.transaction(deadline) as db:
            self._balances(db, key.scope)
            row, _ = self._call(db, key)
            return decode('Reservation', bytes(row['receipt']))

    def admit_v1(self, run, receipt, deadline):
        key = receipt.key
        with self.transaction(deadline) as db:
            # Global admission index/spacing arbitration does not acquire G or A.
            db.execute(text('SELECT pg_advisory_xact_lock(73105, 3)'))
            self._balances(db, key.scope, active=True, at=deadline.check())
            row, core = self._call(db, key, run)
            require(row['state'] == 'RESERVED' and not row['cancelled'] and not row['closed'], 'RESERVATION_UNAVAILABLE')
            require(bytes(row['receipt']) == receipt.encode(), 'RESERVATION_UNAVAILABLE')
            occupied = db.scalar(select(t.admission.c.key_digest).where(~t.admission.c.closed,
                (t.admission.c.account_ref == key.scope.account_ref) | (t.admission.c.deployment_ref == key.scope.deployment_ref)).limit(1))
            require(occupied is None, 'RESERVATION_UNAVAILABLE')
            recent = db.scalar(select(func.max(t.admission.c.admitted_at)).where(
                (t.admission.c.account_ref == key.scope.account_ref) | (t.admission.c.deployment_ref == key.scope.deployment_ref)))
            at = deadline.check((core.valid_from, receipt.expires_at))
            require(recent is None or (at-recent).total_seconds() >= 1, 'RESERVATION_UNAVAILABLE')
            admission = make('Admission', key=key, admission_id=uuid4().hex, owner_generation=run.owner_generation,
                             admitted_at=stamp(at), expires_at=receipt.expires_at)
            db.execute(insert(t.admission).values(key_digest=key.fingerprint(), admission_id=admission.admission_id,
                account_ref=key.scope.account_ref, deployment_ref=key.scope.deployment_ref, owner_generation=run.owner_generation,
                admitted_at=at, expires_at=utc(receipt.expires_at), closed=False))
            db.execute(update(t.reservation).where(t.reservation.c.key_digest == key.fingerprint()).values(state='ADMITTED'))
            self._event(db, row, 'ADMITTED', admission)
        return admission

    def marker(self, run, admission, ticket, deadline):
        key = admission.key
        require(ticket.key_digest == key.fingerprint() and ticket.owner_generation == run.owner_generation)
        with self.transaction(deadline) as db:
            self._balances(db, key.scope, active=True, at=deadline.check())
            row, core = self._call(db, key, run)
            require(row['state'] == 'ADMITTED' and not row['cancelled'], 'RESERVATION_UNAVAILABLE')
            stored = db.execute(select(t.admission).where(t.admission.c.key_digest == key.fingerprint())).mappings().one()
            require(not stored['closed'] and stored['admission_id'] == admission.admission_id
                    and stored['owner_generation'] == run.owner_generation and ticket.body_digest == core.body_digest, 'OWNER_LOST')
            eid = uuid4().hex
            db.execute(insert(t.marker).values(key_digest=key.fingerprint(), event_id=eid,
                owner_generation=run.owner_generation, ticket=ticket.encode(), body_digest=core.body_digest))
            self._event(db, row, 'SEND_INTENT', {'key_digest': key.fingerprint(), 'ticket_id': ticket.ticket_id}, eid)
            db.execute(update(t.reservation).where(t.reservation.c.key_digest == key.fingerprint()).values(state='SEND_INTENT'))
            self.hook('before_marker_commit')
        return eid

    def mark_unknown(self, ctx, key):
        with self.transaction() as db:
            balances = self._balances(db, key.scope)
            row, _ = self._call(db, key, ctx)
            self._retain(db, key, row, balances)
            self._pause(db, key, row, balances)

    def terminal_projection(self,ctx,key,terminal,usage):
        require(terminal in ('NONE','COMPLETED','INCOMPLETE','FAILED','CANCELLED','UNKNOWN'))
        usage=decode('UsageView',usage.encode())
        record=dict(key_digest=key.fingerprint(),owner_generation=ctx.owner_generation,terminal=terminal,usage=usage.document())
        event_id=sha256(canonical(record)).hexdigest()
        with self.transaction() as db:
            self._balances(db,key.scope)
            row,_=self._call(db,key)
            require(ctx.scope==key.scope and ctx.owner_generation<=row['owner_generation'],'OWNER_LOST')
            # Preserve the narrow W1 projection even when subsequent profile or
            # display validation fails. It is evidence, never final settlement
            # authority; only independently correlated endpoint usage settles.
            self._event(db,row,'TERMINAL_PROJECTION',record,event_id)
        return event_id

    def _conflict(self, db, key, row, balances, existing, incoming, reason, incoming_record=None):
        prior = db.scalar(select(t.conflict.c.conflict_id).where(t.conflict.c.key_digest == key.fingerprint(),
            t.conflict.c.existing_digest == existing, t.conflict.c.incoming_digest == incoming, t.conflict.c.reason == reason))
        if prior is None and row['event_count'] < 64:
            cid = uuid4().hex
            db.execute(insert(t.conflict).values(conflict_id=cid, key_digest=key.fingerprint(),
                existing_digest=existing, incoming_digest=incoming, reason=reason))
            self._event(db, row, 'CONFLICT', dict(existing_digest=existing, incoming_digest=incoming, reason=reason,
                incoming=incoming_record.document() if incoming_record is not None else None), cid, recovery=True)
        self._retain(db, key, row, balances, incoming_record)
        self._pause(db, key, row, balances, state='CONFLICT')

    def evidence_prefix(self, db, key, events):
        """Only an immutable S9 receipt can retire a contradictory prefix."""
        raw = db.scalar(select(t.event.c.record).where(t.event.c.key_digest == key.fingerprint(),
            t.event.c.kind == 'RECOVERY_RESOLVED').order_by(t.event.c.sequence.desc()).limit(1))
        if raw is None:
            return 0
        record = bounded_json(bytes(raw), maximum=4096, depth=4, nodes=64)
        require(set(record) == {'format', 'decision_id', 'settlement_id', 'final_event_id',
            'observation_count', 'observation_digest'} and record['format'] == 'ra-w2-recovery-resolution/1', 'CONFLICT')
        count = record['observation_count']
        require(type(count) is int and 1 <= count <= len(events) <= 64
            and sha256(canonical([e.document() for e in events[:count]])).hexdigest() == record['observation_digest'], 'CONFLICT')
        decision = db.execute(select(t.recovery).where(t.recovery.c.decision_id == record['decision_id'],
            t.recovery.c.key_digest == key.fingerprint())).mappings().one_or_none()
        settled = db.execute(select(t.settlement).where(t.settlement.c.settlement_id == record['settlement_id'],
            t.settlement.c.key_digest == key.fingerprint())).mappings().one_or_none()
        require(decision is not None and settled is not None and settled['final_event_id'] == record['final_event_id']
            and any(e.event_id == record['final_event_id'] for e in events[:count]), 'CONFLICT')
        context = decode('RecoveryContext', bytes(decision['record']))
        require(context.key == key and context.owner_generation == decision['owner_generation'], 'CONFLICT')
        final = decode('Settlement', bytes(settled['record']))
        chosen = next(e for e in events[:count] if e.event_id == record['final_event_id'])
        require(chosen.kind in ('FINAL_USAGE', 'ZERO_PROVEN') and chosen.usage.state == 'known'
            and actual(chosen.usage) == (final.settled_tokens, final.settled_microusd)
            and final.key_digest == key.fingerprint() and final.final_event_id == chosen.event_id
            and final.settlement_id == settled['settlement_id'] and final.revision == settled['revision'], 'CONFLICT')
        latest = db.scalar(select(t.settlement.c.settlement_id).where(t.settlement.c.key_digest == key.fingerprint())
            .order_by(t.settlement.c.revision.desc()).limit(1))
        require(latest == settled['settlement_id'], 'CONFLICT')
        postings = list(db.execute(select(t.posting).where(t.posting.c.settlement_id == settled['settlement_id'])).mappings())
        require(len(postings) == 4 and {(p['scope_kind'], p['dimension']) for p in postings}
            == {(kind, dimension) for kind in ('account', 'task') for dimension in ('tokens', 'microusd')}
            and all(p['balance_id'] == balance_id(key.scope, p['scope_kind']) for p in postings), 'CONFLICT')
        return count

    def unresolved_conflict(self, db, key):
        latest = db.scalar(select(func.max(t.event.c.sequence)).where(t.event.c.key_digest == key.fingerprint(),
            t.event.c.kind == 'RECOVERY_RESOLVED')) or 0
        return bool(db.scalar(select(func.count()).select_from(t.event).where(t.event.c.key_digest == key.fingerprint(),
            t.event.c.kind == 'CONFLICT', t.event.c.sequence > latest)))

    def evidence_hazards(self, events, prefix=0, known=None):
        """Scan the bounded producer inventory, independent of selected copies."""
        hazards = []
        for event in events[prefix:]:
            if event.kind in ('CONFLICT', 'COVERAGE_GAP'):
                hazards.append(('EVENT_CONFLICT' if event.kind == 'CONFLICT' else 'COVERAGE_GAP', event))
            elif event.kind == 'FINAL_USAGE':
                if event.usage.state != 'known':
                    hazards.append(('USAGE_UNKNOWN', event))
                else:
                    try:
                        value = actual(event.usage)
                    except PortError:
                        hazards.append(('USAGE_OVERFLOW', event))
                        continue
                    if known is not None and known != value:
                        hazards.append(('FINAL_CONFLICT', event))
                    known = value
        return hazards

    def reconcile_v1(self, ctx, key, observation_ids, observer, *, recovery_decision=None):
        require(type(observation_ids) in (list, tuple) and 1 <= len(observation_ids) <= 8
                and len(set(observation_ids)) == len(observation_ids), 'LIMIT_EXCEEDED')
        # Fetch from the independent producer, never trust callback-supplied usage.
        inventory = tuple(observer.events(key))
        require(len(inventory) <= 64 and len({e.event_id for e in inventory}) == len(inventory), 'OBSERVER_UNAVAILABLE')
        events = {e.event_id: e for e in inventory}
        require(all(i in events for i in observation_ids), 'OBSERVER_UNAVAILABLE')
        selected = [decode('Observation', events[i].encode()) for i in observation_ids]
        read=ctx.read if ctx._name=='RecoveryContext' else make('ReadContext',scope=ctx.scope,
            process_epoch=ctx.process_epoch,deadline_at=ctx.deadline_at,mono_deadline_ns=ctx.mono_deadline_ns,
            cancellation_id=ctx.cancellation_id)
        # Producer acknowledgement is obtained before SQL locks. Its durable
        # journal projection must match each supplied event, not just an in-
        # memory callback value or agreement between two SQL ledgers.
        observe_until = monotonic()+3
        try:
            for event in inventory:
                ack=observer.observe_v1(read,event,max(0, observe_until-monotonic()))
                require((ack.event_id,ack.sequence,ack.event_digest)==(event.event_id,event.sequence,event.fingerprint()),
                        'OBSERVER_UNAVAILABLE')
        except PortError:
            self.mark_unknown(ctx, key)
            raise
        if tuple(observer.events(key)) != inventory:
            self.mark_unknown(ctx, key)
            raise PortError('OBSERVER_UNAVAILABLE', key.fingerprint())
        conflict_found = False
        result = None
        with self.transaction() as db:
            balances = self._balances(db, key.scope)
            row, core = self._call(db, key, ctx)
            is_recovery = ctx._name == 'RecoveryContext'
            if is_recovery:
                decision = db.execute(select(t.recovery).where(t.recovery.c.decision_id == ctx.recovery_decision_id)).mappings().one_or_none()
                require(decision is not None and decision['key_digest'] == key.fingerprint()
                        and decision['owner_generation'] == ctx.owner_generation, 'OWNER_LOST')
            prefix = self.evidence_prefix(db, key, inventory)
            retired = {e.event_id for e in inventory[:prefix]}
            hazards = self.evidence_hazards(inventory, prefix,
                (row['actual_tokens'], row['actual_microusd']) if row['revision'] else None)
            hard_conflict = (row['state'] == 'CONFLICT' or self.unresolved_conflict(db, key)
                or any(reason != 'USAGE_UNKNOWN' for reason, _ in hazards))
            resolving = (is_recovery and (bool(hazards) or hard_conflict or row['state'] != 'SETTLED')
                and (not hard_conflict or recovery_decision == ctx.recovery_decision_id))
            if resolving:
                # One decision chooses one exact final and inventory prefix.
                # It cannot authorize subsequently arriving contradictions.
                used = list(db.scalars(select(t.event.c.record).where(t.event.c.key_digest == key.fingerprint(),
                    t.event.c.kind == 'RECOVERY_RESOLVED')))
                resolving = not any(bounded_json(bytes(raw), maximum=4096, depth=4, nodes=64)['decision_id']
                    == ctx.recovery_decision_id for raw in used)
            for reason, incoming in hazards:
                if reason == 'USAGE_UNKNOWN':
                    if not resolving:
                        self._retain(db, key, row, balances, incoming)
                        self._pause(db, key, row, balances)
                else:
                    self._conflict(db, key, row, balances, incoming.evidence_digest,
                        incoming.fingerprint(), reason, incoming)
                    conflict_found = not resolving
            blocked = (bool(hazards) or hard_conflict) and not resolving
            if blocked:
                # Every known lower bound matters, including an earlier larger
                # final whose later contradictory receipt is smaller.
                for incoming in inventory[prefix:]:
                    if incoming.kind in ('FINAL_USAGE', 'CONFLICT'):
                        self._retain(db, key, row, balances, incoming)
                self._pause(db, key, row, balances, state='CONFLICT' if hard_conflict else 'IN_DOUBT')
            conflict_found |= row['state'] == 'CONFLICT' and not resolving
            if resolving:
                require(sum(e.kind in ('FINAL_USAGE', 'ZERO_PROVEN') for e in selected) <= 1, 'CONFLICT')
            for event in selected:
                require(event.key_digest == key.fingerprint() and event.call_ref == key.call_ref
                        and event.body_digest == core.body_digest, 'CONFLICT')
                require(is_recovery or event.owner_generation == ctx.owner_generation, 'OWNER_LOST')
                if event.event_id in retired and not resolving:
                    continue
                ledger = db.execute(select(t.event).where(t.event.c.event_id == event.event_id)).mappings().one_or_none()
                if ledger is not None and (ledger['key_digest'] != key.fingerprint() or bytes(ledger['record']) != event.encode()):
                    self._conflict(db, key, row, balances, ledger['digest'], event.fingerprint(), 'EVENT_CONFLICT', event)
                    conflict_found = blocked = True
                    continue
                prior = db.execute(select(t.observation_copy).where(
                    (t.observation_copy.c.event_id == event.event_id) |
                    and_(t.observation_copy.c.observer_epoch == event.observer_epoch,
                         t.observation_copy.c.sequence == event.sequence))).mappings().one_or_none()
                if prior is not None:
                    if bytes(prior['record']) != event.encode():
                        self._conflict(db, key, row, balances, prior['digest'], event.fingerprint(), 'EVENT_CONFLICT',event)
                        conflict_found = True
                        blocked = True
                        continue
                else:
                    if row['event_count'] >= 64:
                        self._retain(db, key, row, balances, event)
                        self._pause(db, key, row, balances, state='CONFLICT')
                        conflict_found = True
                        blocked = True
                        continue
                    db.execute(insert(t.observation_copy).values(event_id=event.event_id, key_digest=key.fingerprint(),
                        observer_epoch=event.observer_epoch, sequence=event.sequence, digest=event.fingerprint(), record=event.encode()))
                    self._event(db, row, event.kind, event, event.event_id, recovery=True)
                if event.kind == 'STREAM_CLOSED':
                    row['closed'] = True
                    db.execute(update(t.reservation).where(t.reservation.c.key_digest == key.fingerprint()).values(closed=True))
                if event.kind not in ('FINAL_USAGE', 'ZERO_PROVEN'):
                    continue
                if blocked:
                    continue
                if not observer.coverage() or tuple(observer.events(key)) != inventory:
                    self._conflict(db, key, row, balances, event.evidence_digest,
                        event.fingerprint(), 'COVERAGE_GAP', event)
                    conflict_found = blocked = True
                    continue
                if event.kind == 'ZERO_PROVEN':
                    require(observer.calls.get(key.fingerprint(), {}).get('closed') is True
                            and observer.witness.accepted[key.fingerprint()] == 0, 'OBSERVER_UNAVAILABLE')
                else:
                    require(observer.witness.accepted[key.fingerprint()] == 1
                            and event.terminal in ('COMPLETED', 'INCOMPLETE', 'FAILED', 'CANCELLED'), 'OBSERVER_UNAVAILABLE')
                if event.usage.state != 'known':
                    self._retain(db, key, row, balances, event)
                    self._pause(db, key, row, balances)
                    continue
                try:
                    tokens, cost = actual(event.usage)
                except PortError:
                    self._conflict(db, key, row, balances, event.evidence_digest,
                        event.fingerprint(), 'USAGE_OVERFLOW', event)
                    # Preserve the original bounded counts in the immutable copy.
                    conflict_found = True
                    continue
                prior_settlement = db.execute(select(t.settlement).where(t.settlement.c.key_digest == key.fingerprint())
                    .order_by(t.settlement.c.revision.desc()).limit(1)).mappings().one_or_none()
                if prior_settlement:
                    previous = decode('Settlement', bytes(prior_settlement['record']))
                    if ((tokens, cost) == (row['actual_tokens'], row['actual_microusd'])
                            and row['state'] == 'SETTLED' and not resolving):
                        result = previous
                        continue
                    if not resolving:
                        self._conflict(db, key, row, balances, previous.accounting_digest, event.fingerprint(), 'FINAL_CONFLICT',event)
                        conflict_found = True
                        blocked = True
                        continue
                elif row['state'] == 'CONFLICT' and not resolving:
                    conflict_found = True
                    continue
                held_t, held_c = row['held_tokens'], row['held_microusd']
                delta_t, delta_c = tokens-row['actual_tokens'], cost-row['actual_microusd']
                if any(not (0 <= b['settled_tokens']+delta_t <= MAX_N and 0 <= b['settled_microusd']+delta_c <= MAX_N)
                       for b, _ in balances):
                    self._conflict(db, key, row, balances, event.evidence_digest,
                        event.fingerprint(), 'BALANCE_OVERFLOW', event)
                    conflict_found = True
                    blocked = True
                    continue
                revision = row['revision']+1
                sid = uuid4().hex
                totals = dict(key_digest=key.fingerprint(), final_event_id=event.event_id, revision=revision,
                              tokens=tokens, microusd=cost, settled_delta=[delta_t, delta_c], held_delta=[-held_t, -held_c])
                result = make('Settlement', key_digest=key.fingerprint(), settlement_id=sid, final_event_id=event.event_id,
                    revision=revision, state='ZERO' if event.kind == 'ZERO_PROVEN' else 'KNOWN',
                    settled_tokens=tokens, settled_microusd=cost, held_tokens=0, held_microusd=0,
                    refund_tokens=max(held_t-delta_t, 0), refund_microusd=max(held_c-delta_c, 0),
                    overrun_tokens=max(delta_t-held_t, 0), overrun_microusd=max(delta_c-held_c, 0),
                    accounting_digest=sha256(canonical(totals)).hexdigest())
                db.execute(insert(t.settlement).values(settlement_id=sid, key_digest=key.fingerprint(),
                    final_event_id=event.event_id, revision=revision, record=result.encode()))
                for b, policy in balances:
                    for dimension, delta, held in (('tokens', delta_t, held_t), ('microusd', delta_c, held_c)):
                        db.execute(insert(t.posting).values(settlement_id=sid, scope_kind=policy['scope_kind'], dimension=dimension,
                            balance_id=b['balance_id'], settled_delta=delta, held_delta=-held))
                    b['settled_tokens'] += delta_t
                    b['settled_microusd'] += delta_c
                    b['held_tokens'] -= held_t
                    b['held_microusd'] -= held_c
                    if ((policy['token_cap'] is not None and b['settled_tokens']+b['held_tokens'] > policy['token_cap'])
                        or (policy['cost_cap'] is not None and b['settled_microusd']+b['held_microusd'] > policy['cost_cap'])):
                        b['state'] = 'PAUSED_UNKNOWN' if b['state'] != 'CANCELLED' else 'CANCELLED'
                    db.execute(update(t.balance).where(t.balance.c.balance_id == b['balance_id']).values(**{k: b[k] for k in
                        ('settled_tokens', 'settled_microusd', 'held_tokens', 'held_microusd', 'state')}))
                row.update(state='SETTLED', actual_tokens=tokens, actual_microusd=cost, held_tokens=0, held_microusd=0, revision=revision)
                db.execute(update(t.reservation).where(t.reservation.c.key_digest == key.fingerprint()).values(**{k: row[k] for k in
                    ('state', 'actual_tokens', 'actual_microusd', 'held_tokens', 'held_microusd', 'revision')}))
                if resolving:
                    self._event(db, row, 'RECOVERY_RESOLVED', dict(format='ra-w2-recovery-resolution/1',
                        decision_id=ctx.recovery_decision_id, settlement_id=sid, final_event_id=event.event_id,
                        observation_count=len(inventory),
                        observation_digest=sha256(canonical([e.document() for e in inventory])).hexdigest()), recovery=True)
                    retired.update(e.event_id for e in inventory)
                    resolving = False
                self.hook('before_settlement_commit')
            if result is None and not conflict_found and row['state'] != 'SETTLED':
                self._pause(db, key, row, balances)
        if conflict_found:
            raise PortError('CONFLICT', key.fingerprint())
        if tuple(observer.events(key)) != inventory or not observer.coverage():
            self.mark_unknown(ctx, key)
        # Re-read the committed disposition: a later event in the batch can
        # invalidate a previously constructed result without undoing its S.
        return self.current_settlement(key)

    def current_settlement(self, key):
        with self.transaction() as db:
            self._balances(db, key.scope)
            row, _ = self._call(db, key)
            if row['state'] == 'SETTLED':
                raw = db.scalar(select(t.settlement.c.record).where(t.settlement.c.key_digest == key.fingerprint())
                    .order_by(t.settlement.c.revision.desc()).limit(1))
                require(raw is not None, 'CONFLICT')
                return decode('Settlement', bytes(raw))
            return make('Settlement', key_digest=key.fingerprint(), settlement_id='unresolved', final_event_id='unknown',
                revision=max(1, row['revision']), state='CONFLICT' if row['state'] == 'CONFLICT' else 'UNKNOWN',
                settled_tokens=row['actual_tokens'], settled_microusd=row['actual_microusd'],
                held_tokens=row['held_tokens'], held_microusd=row['held_microusd'], refund_tokens=0, refund_microusd=0,
                overrun_tokens=0, overrun_microusd=0, accounting_digest=sha256(canonical([key.fingerprint(), row['revision']])).hexdigest())

    def finish(self, ctx, key, observer):
        require(observer.coverage() and observer.calls.get(key.fingerprint(), {}).get('closed') is True, 'OBSERVER_UNAVAILABLE')
        with self.transaction() as db:
            self._balances(db, key.scope)
            row, _ = self._call(db, key, ctx)
            db.execute(update(t.admission).where(t.admission.c.key_digest == key.fingerprint(),
                t.admission.c.owner_generation == ctx.owner_generation).values(closed=True))
            db.execute(update(t.reservation).where(t.reservation.c.key_digest == key.fingerprint()).values(closed=True))

    def persist_completion(self, ctx, key, completion):
        require(completion.key_digest == key.fingerprint() and completion.display_state == 'SUPPRESSED')
        with self.transaction() as db:
            self._balances(db, key.scope)
            row, _ = self._call(db, key, ctx)
            old = db.scalar(select(t.completion.c.digest).where(t.completion.c.digest == completion.fingerprint()))
            if old is None:
                self._event(db, row, 'COMPLETION', completion, completion.completion_id, recovery=True)
                db.execute(insert(t.completion).values(completion_id=completion.completion_id, key_digest=key.fingerprint(),
                                                       digest=completion.fingerprint(), record=completion.encode()))
