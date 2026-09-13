"""Explicit bounded recovery. Reopening never grants a replay or stale display."""
from dataclasses import dataclass
from datetime import timedelta
from hashlib import sha256
from uuid import uuid4

from sqlalchemy import select, insert, update, text, func
from app.ai.proposals.codec import canonical
from . import schema as t
from .accounting import balance_id
from .guard import WRITER_FENCE_KEY
from .records import make, decode, require, PortError, utc, stamp, _scalar


@dataclass(frozen=True)
class ReopenEvidence:
    request: object
    checked_at: object
    expires_at: object
    pending_digest: str


class Recovery:
    def __init__(self, store, authority, writer):
        self.store, self.authority, self.writer = store, authority, writer

    def _inventory(self, deadline):
        """Compare restored SQL conservation with independently retained calls.

        Unresolved calls pause their budgets even if a restored balance says
        ACTIVE. Missing bindings/postings are not reconstructed by agreement
        between the result and accounting ledgers.
        """
        mismatch=False
        with self.store.transaction(deadline) as db:
            balances=list(db.execute(select(t.balance,t.policy.c.scope_kind).join(t.policy)
                .order_by(t.policy.c.scope_kind,t.balance.c.balance_id).limit(8193)
                .with_for_update(of=t.balance)).mappings())
            require(len(balances)<=8192,'LIMIT_EXCEEDED')
            rows=list(db.execute(select(t.core,t.reservation.c.state,t.reservation.c.actual_tokens,
                t.reservation.c.actual_microusd,t.reservation.c.held_tokens,t.reservation.c.held_microusd)
                .join(t.reservation).limit(262145)).mappings())
            require(len(rows)<=4096*64,'LIMIT_EXCEEDED')
            totals={b['balance_id']:[0,0,0,0,0] for b in balances}
            paused=set()
            for row in rows:
                digest=row['key_digest'];call=self.authority.calls.get(digest)
                evidence=self.authority.core_evidence.get(digest)
                key=decode('ReservationKey',bytes(row['key_bytes']))
                require(call is not None and evidence is not None,'OBSERVER_UNAVAILABLE')
                if (canonical(evidence['core'])!=bytes(row['core_bytes']) or canonical(evidence['key'])!=key.encode()
                    or key.fingerprint()!=digest):mismatch=True
                marker=db.scalar(select(t.marker.c.event_id).where(t.marker.c.key_digest==digest))
                repaired=db.scalar(select(t.recovery.c.decision_id).where(t.recovery.c.key_digest==digest).limit(1))
                if call['marked'] and marker is None and repaired is None:mismatch=True
                if deadline.check()>=call['expiry']+timedelta(days=90):mismatch=True
                settlements=list(db.execute(select(t.settlement).where(t.settlement.c.key_digest==digest)
                    .order_by(t.settlement.c.revision)).mappings())
                settled_postings={kind:{dimension:0 for dimension in ('tokens','microusd')} for kind in ('account','task')}
                for settlement in settlements:
                    postings=list(db.execute(select(t.posting).where(t.posting.c.settlement_id==settlement['settlement_id'])).mappings())
                    expected={(kind,dimension) for kind in ('account','task') for dimension in ('tokens','microusd')}
                    if len(postings)!=4 or {(p['scope_kind'],p['dimension']) for p in postings}!=expected:mismatch=True
                    for p in postings:
                        if p['balance_id']!=row[p['scope_kind']+'_balance']:mismatch=True
                        settled_postings[p['scope_kind']][p['dimension']]+=p['settled_delta']
                if any((p['tokens'],p['microusd'])!=(row['actual_tokens'],row['actual_microusd'])
                    for p in settled_postings.values()):mismatch=True
                if row['state']=='SETTLED':
                    if not settlements:mismatch=True
                    else:
                        final=decode('Settlement',bytes(settlements[-1]['record']))
                        if (final.settled_tokens,final.settled_microusd)!=(row['actual_tokens'],row['actual_microusd']):mismatch=True
                unresolved=row['state']!='SETTLED'
                events = self.authority.events(key)
                try:
                    prefix = self.store.evidence_prefix(db, key, events)
                    unresolved |= bool(self.store.evidence_hazards(events, prefix,
                        (row['actual_tokens'], row['actual_microusd'])))
                    unresolved |= self.store.unresolved_conflict(db, key)
                except PortError:
                    mismatch = unresolved = True
                for field in ('account_balance','task_balance'):
                    bid=row[field]
                    require(bid in totals,'CONFLICT')
                    for i,k in enumerate(('actual_tokens','actual_microusd','held_tokens','held_microusd')):
                        totals[bid][i]+=row[k]
                    totals[bid][4]+=1
                    if unresolved:paused.add(bid)
            if set(self.authority.calls)!={r['key_digest'] for r in rows}:mismatch=True
            for b in balances:
                amounts=totals[b['balance_id']]
                if tuple(amounts[:4])!=tuple(b[k] for k in ('settled_tokens','settled_microusd','held_tokens','held_microusd')):
                    mismatch=True
                if b['calls']<amounts[4] or b['wall_ns']<amounts[4]*30_000_000_000:mismatch=True
            if mismatch:paused={b['balance_id'] for b in balances}
            if paused:
                db.execute(update(t.balance).where(t.balance.c.balance_id.in_(paused),t.balance.c.state!='CANCELLED')
                    .values(state='PAUSED_UNKNOWN'))
        require(not mismatch,'OBSERVER_UNAVAILABLE')
        return rows

    def resume_budgets(self,read,decision_id,deadline,*,kinds=('account','task')):
        """Explicitly resume resolved scopes, preserving cancellation and caps.

        This never adds budget, resets call counts, removes liability or opens
        the acceptance gate. Reopening still requires its separate evidence.
        """
        require(_scalar('Id',decision_id) and kinds in (('account',),('task',),('account','task')))
        record=dict(format='ra-w2-budget-resume/1',scope=read.scope.document(),decision_id=decision_id,kinds=list(kinds))
        digest=sha256(canonical(record)).hexdigest()
        with self.writer.mutation('RECOVERY',decision_id,digest,deadline) as operation:
            # A restored SETTLED projection is insufficient. Compare all
            # independent observations and exact correction provenance first.
            # Inventory commits pauses even if the following resume is denied.
            rows = self._inventory(deadline)
            require(self.authority.coverage(), 'OBSERVER_UNAVAILABLE')
            with self.store.transaction(deadline) as db:
                operation.bind(db)
                balances=self.store._balances(db,read.scope)
                for b,policy in balances:
                    if policy['scope_kind'] not in kinds:continue
                    column=t.core.c.account_balance if policy['scope_kind']=='account' else t.core.c.task_balance
                    require(b['state'] in ('ACTIVE','PAUSED_UNKNOWN') and b['held_tokens']==b['held_microusd']==0,
                        'RESERVATION_UNAVAILABLE')
                    require(all(policy[k] is not None for k in ('token_cap','cost_cap','call_cap','wall_cap_ns'))
                        and b['settled_tokens']<=policy['token_cap'] and b['settled_microusd']<=policy['cost_cap']
                        and b['calls']<=policy['call_cap'] and b['wall_ns']<=policy['wall_cap_ns']
                        and policy['valid_from']<=deadline.check()<policy['expires_at'],'RESERVATION_UNAVAILABLE')
                    require(db.scalar(select(func.count()).select_from(t.core.join(t.reservation))
                        .where(column==b['balance_id'],t.reservation.c.state!='SETTLED'))==0,'COMMIT_UNKNOWN')
                    for row in rows:
                        if row[policy['scope_kind']+'_balance'] != b['balance_id']:continue
                        key = decode('ReservationKey', bytes(row['key_bytes']))
                        current, _ = self.store._call(db, key)
                        events = self.authority.events(key)
                        prefix = self.store.evidence_prefix(db, key, events)
                        require(not self.store.evidence_hazards(events, prefix,
                            (current['actual_tokens'], current['actual_microusd']))
                            and not self.store.unresolved_conflict(db, key), 'COMMIT_UNKNOWN')
                    require(db.scalar(select(func.count()).select_from(t.core.join(t.admission))
                        .where(column==b['balance_id'],~t.admission.c.closed))==0,'COMMIT_UNKNOWN')
                    db.execute(update(t.balance).where(t.balance.c.balance_id==b['balance_id']).values(state='ACTIVE'))
                db.execute(insert(t.recovery).values(decision_id=decision_id,deployment_ref=read.scope.deployment_ref,
                    key_digest='0'*64,owner_generation=read.scope.context_generation,evidence_digest=digest,record=canonical(record)))
            operation.committed()

    def recover_call(self, read, key, decision_id, expected_generation, owner_id, deadline):
        require(_scalar('Id', decision_id) and _scalar('Id', owner_id) and read.scope == key.scope)
        digest = sha256(canonical([decision_id, key.document(), expected_generation, owner_id])).hexdigest()
        with self.writer.mutation('RECOVERY', decision_id, digest, deadline) as ticket:
            # Closing the actual authoritative stream is independent of DB
            # generation. No successor is admitted by this operation.
            events = self.authority.close_call(key, deadline.remaining(3))
            require(self.authority.coverage() and any(e.kind == 'STREAM_CLOSED' for e in events), 'OBSERVER_UNAVAILABLE')
            evidence_digest = sha256(canonical([e.document() for e in events])).hexdigest()
            with self.store.transaction(deadline) as db:
                ticket.bind(db)
                self.store._balances(db, key.scope)
                row, _ = self.store._call(db, key)
                require(row['owner_generation'] == expected_generation < 2147483647, 'OWNER_LOST')
                prior = db.execute(select(t.recovery).where(t.recovery.c.decision_id == decision_id)).mappings().one_or_none()
                require(prior is None, 'CONFLICT')
                ctx = make('RecoveryContext', read=read, key=key, recovery_decision_id=decision_id,
                           owner_id=owner_id, owner_generation=expected_generation+1)
                db.execute(insert(t.recovery).values(decision_id=decision_id, deployment_ref=key.scope.deployment_ref,
                    key_digest=key.fingerprint(), owner_generation=ctx.owner_generation,
                    evidence_digest=evidence_digest, record=ctx.encode()))
                db.execute(update(t.reservation).where(t.reservation.c.key_digest == key.fingerprint(),
                    t.reservation.c.owner_generation == expected_generation).values(owner_id=owner_id,
                        owner_generation=ctx.owner_generation, closed=True))
                db.execute(update(t.admission).where(t.admission.c.key_digest == key.fingerprint(),
                    t.admission.c.owner_generation == expected_generation).values(closed=True))
            ticket.committed()
        return ctx

    def restore_liability(self, read, key, decision_id, owner_id, deadline):
        """Repair an omitted call from independent immutable parent evidence.

        It restores reserved liability, never a settled zero or display. Existing
        postings are not overwritten. Ambiguous partial balance restorations
        stay paused for separate evidence reconciliation.
        """
        require(read.scope == key.scope and _scalar('Id', decision_id) and _scalar('Id', owner_id))
        evidence = self.authority.core_evidence.get(key.fingerprint())
        require(evidence is not None and self.authority.coverage(), 'OBSERVER_UNAVAILABLE')
        core = decode('BindingCore', canonical(evidence['core']))
        receipt = decode('Reservation', canonical(evidence['reservation']))
        require(core.fingerprint() == key.core_digest and receipt.key == key, 'CONFLICT')
        ctx = make('RecoveryContext',read=read,key=key,recovery_decision_id=decision_id,
            owner_id=owner_id,owner_generation=receipt.owner_generation+1)
        digest = sha256(canonical(evidence)).hexdigest()
        with self.writer.mutation('RECOVERY',decision_id,digest,deadline) as operation:
            self.authority.close_call(key,deadline.remaining(3))
            with self.store.transaction(deadline) as db:
                operation.bind(db)
                balances = self.store._balances(db,key.scope)
                old = db.execute(select(t.core).where(t.core.c.key_digest==key.fingerprint())).mappings().one_or_none()
                if old is not None:
                    require(bytes(old['key_bytes'])==key.encode() and bytes(old['core_bytes'])==core.encode(),'CONFLICT')
                require(db.scalar(select(t.reservation.c.key_digest).where(t.reservation.c.key_digest==key.fingerprint())) is None,'CONFLICT')
                for b,policy in balances:
                    column = t.core.c.account_balance if policy['scope_kind']=='account' else t.core.c.task_balance
                    totals = db.execute(select(func.coalesce(func.sum(t.reservation.c.held_tokens),0),
                        func.coalesce(func.sum(t.reservation.c.held_microusd),0),func.count())
                        .select_from(t.core.join(t.reservation)).where(column==b['balance_id'])).one()
                    settled = db.execute(select(func.coalesce(func.sum(t.reservation.c.actual_tokens),0),
                        func.coalesce(func.sum(t.reservation.c.actual_microusd),0))
                        .select_from(t.core.join(t.reservation)).where(column==b['balance_id'])).one()
                    # A surviving debit without its call cannot be safely
                    # attributed. Do not create another full debit on top of
                    # it when the independently observed receipt arrives.
                    require((b['settled_tokens'],b['settled_microusd'])==tuple(settled),'CONFLICT')
                    # The only supported restorations are a complete earlier
                    # balance snapshot or a surviving exact reserved balance.
                    require((b['held_tokens'],b['held_microusd']) in
                        ((totals[0],totals[1]),(totals[0]+receipt.reserved_tokens,totals[1]+receipt.reserved_microusd)), 'CONFLICT')
                    db.execute(update(t.balance).where(t.balance.c.balance_id==b['balance_id']).values(
                        held_tokens=totals[0]+receipt.reserved_tokens,held_microusd=totals[1]+receipt.reserved_microusd,
                        calls=max(b['calls'],totals[2]+1),wall_ns=max(b['wall_ns'],(totals[2]+1)*30_000_000_000),
                        state='CANCELLED' if b['state']=='CANCELLED' else 'PAUSED_UNKNOWN'))
                if old is None:
                    db.execute(insert(t.core).values(key_digest=key.fingerprint(),key_bytes=key.encode(),core_bytes=core.encode(),
                        task_id=key.scope.task_id,case_id=key.scope.case_id,question_id=core.question_id,call_ref=key.call_ref,
                        account_ref=key.scope.account_ref,deployment_ref=key.scope.deployment_ref,
                        account_balance=balance_id(key.scope,'account'),task_balance=balance_id(key.scope,'task')))
                row=dict(key_digest=key.fingerprint(),reservation_id=receipt.reservation_id,receipt=receipt.encode(),
                    owner_id=owner_id,owner_generation=ctx.owner_generation,state='IN_DOUBT',held_tokens=receipt.reserved_tokens,
                    held_microusd=receipt.reserved_microusd,actual_tokens=0,actual_microusd=0,event_count=0,closed=True,
                    cancelled=balances[1][0]['state']=='CANCELLED',revision=0)
                db.execute(insert(t.reservation).values(**row))
                self.store._event(db,row,'RESTORED_LIABILITY',dict(evidence_digest=digest,decision_id=decision_id),recovery=True)
                db.execute(insert(t.recovery).values(decision_id=decision_id,deployment_ref=key.scope.deployment_ref,
                    key_digest=key.fingerprint(),owner_generation=ctx.owner_generation,evidence_digest=digest,record=ctx.encode()))
            operation.committed()
        return ctx

    def reopen(self, context, decision_id, deadline, qualification):
        require(_scalar('Id', decision_id))
        authority = self.authority
        with self.writer.guard.read(deadline) as token:
            with authority.locked(deadline.remaining()):
                require(authority.state in ('CLOSED','RECOVERY_REQUIRED'),'CONTEXT_CHANGED')
                epoch, generation = authority.epoch, authority.generation
            deadline.check()
            # No open/idle writer transaction may later commit after a rollback
            # declaration. Lost G alone supplies no such proof.
            with self.store.transaction(deadline) as db:
                unfinished = db.scalar(text("""SELECT count(*) FROM pg_stat_activity
                    WHERE datname=current_database() AND pid<>pg_backend_pid()
                    AND backend_type='client backend' AND
                    (backend_xid IS NOT NULL OR state LIKE 'idle in transaction%')"""))
                require(unfinished == 0, 'COMMIT_UNKNOWN')
                # Drain writers that bound before this fence and reject old
                # invocations that have checked G but have not started SQL yet.
                # The durable fence commits before inventory/qualification/A;
                # it is retained even if later recovery fails or loses its ack.
                require(db.scalar(select(t.deployment.c.deployment_ref)
                    .where(t.deployment.c.deployment_ref == authority.deployment)
                    .with_for_update()) == authority.deployment, 'AUTHORITY_UNAVAILABLE')
                fence = make('WriterFence', deployment_ref=authority.deployment,
                    recovery_decision_id=decision_id, authority_epoch=epoch,
                    acceptance_generation=generation)
                db.execute(insert(t.recovery).values(decision_id=uuid4().hex,
                    deployment_ref=authority.deployment, key_digest=WRITER_FENCE_KEY,
                    owner_generation=generation, evidence_digest=fence.fingerprint(), record=fence.encode()))
                rows = {row['operation_id']: dict(row) for row in db.execute(select(t.invalidation)).mappings()}
                existing_calls = list(db.execute(select(t.core.c.key_digest, t.core.c.key_bytes,
                    t.core.c.core_bytes, t.reservation.c.owner_generation, t.reservation.c.state)
                    .join(t.reservation)).mappings())
            require(len(existing_calls) <= 4096*64, 'LIMIT_EXCEEDED')
            try:
                self._inventory(deadline)
                require(all(key in authority.operations and authority.operations[key].operation_digest==row['operation_digest']
                    for key,row in rows.items()),'OBSERVER_UNAVAILABLE')
                # A transaction marker and the lifecycle mutation commit
                # atomically. A previously committed independent disposition
                # missing from a restored database cannot be silently treated
                # as current eligibility. Rolled-back history cannot have a
                # committed marker either.
                require(all((operation.operation_id in rows)==(operation.disposition=='COMMITTED')
                    for operation in authority.operations.values()
                    if operation.disposition in ('COMMITTED','ROLLED_BACK')),'OBSERVER_UNAVAILABLE')
            except BaseException:
                self.writer.pause_admission()
                raise
            for operation in tuple(authority.operations.values()):
                if operation.disposition in ('COMMITTED', 'ROLLED_BACK'):
                    continue
                row = rows.get(operation.operation_id)
                if row is not None:
                    require(row['operation_digest'] == operation.operation_digest, 'CONFLICT')
                    outcome = 'COMMITTED'
                else:
                    # G is owned here, no writer transaction remains, and every
                    # supported writer verifies its original G session before
                    # beginning SQL. The old invocation cannot later bind.
                    outcome = 'ROLLED_BACK'
                resolution = make('InvalidationResolution', operation_id=operation.operation_id,
                    operation_digest=operation.operation_digest, deployment_ref=authority.deployment,
                    barrier_digest=operation.event_digest, database_outcome=outcome,
                    transaction_evidence_ref=operation.operation_id)
                authority.resolve_invalidation_v1(context, resolution, (outcome, operation.operation_id), deadline.remaining())
                deadline.check()
            for row in existing_calls:
                key = decode('ReservationKey', bytes(row['key_bytes']))
                require(key.fingerprint() == row['key_digest'], 'CONFLICT')
                require(key.fingerprint() in authority.calls, 'OBSERVER_UNAVAILABLE')
                authority.close_call(key, deadline.remaining(3))
            # A restoration that omitted a core/reservation cannot reopen on
            # ledger agreement: independently registered endpoint calls expose it.
            require({r['key_digest'] for r in existing_calls} == set(authority.calls), 'OBSERVER_UNAVAILABLE')
            require(authority.coverage(), 'OBSERVER_UNAVAILABLE')
            qualification_digest = qualification(token, deadline)
            require(_scalar('Hash', qualification_digest), 'AUTHORITY_UNAVAILABLE')
            pending = sha256(canonical(sorted((a.operation_id, a.operation_digest, a.disposition)
                                    for a in authority.operations.values()))).hexdigest()
            tx_digest = sha256(canonical(sorted((r['operation_id'], r['operation_digest'], r['transaction_id']) for r in rows.values()))).hexdigest()
            closure = sha256(canonical(sorted((key, [e.fingerprint() for e in authority.events(c['key'])
                                  if e.kind == 'STREAM_CLOSED']) for key, c in authority.calls.items()))).hexdigest()
            request = make('ReopenRequest', deployment_ref=authority.deployment, recovery_decision_id=decision_id,
                expected_authority_epoch=epoch, expected_acceptance_generation=generation,
                pending_set_digest=pending, transaction_evidence_digest=tx_digest,
                qualification_digest=qualification_digest, stream_closure_evidence_digest=closure)
            # Record the explicit decision before asking A. No DB transaction
            # remains while reopening serializes against acceptance/invalidation.
            with self.store.transaction(deadline) as db:
                require(db.scalar(select(t.recovery.c.decision_id).where(t.recovery.c.decision_id == decision_id)) is None, 'CONFLICT')
                db.execute(insert(t.recovery).values(decision_id=decision_id, deployment_ref=authority.deployment,
                    key_digest='0'*64, owner_generation=generation,
                    evidence_digest=request.fingerprint(), record=request.encode()))
            checked = deadline.check()
            evidence = ReopenEvidence(request, checked,
                min(utc(context.deadline_at), deadline.expires_at or utc(context.deadline_at)), pending)
            ack = authority.reopen_v1(context, request, evidence, deadline.remaining())
            deadline.check()
            return ack

    def cancel_v1(self, read, key, decision_id, deadline, cancellation):
        require(read.scope == key.scope and _scalar('Id', decision_id))
        # The local latch is immediate; durable cancellation uses a separately
        # scoped writer deadline so setting the latch does not cancel its cleanup.
        cancellation.set(key.scope.task_id)
        if key.fingerprint() in self.authority.calls:
            self.authority.calls[key.fingerprint()]['cancelled'] = True
        digest = sha256(canonical([key.document(), decision_id])).hexdigest()
        with self.writer.mutation('CANCELLATION', decision_id, digest, deadline) as ticket:
            with self.store.transaction(deadline) as db:
                ticket.bind(db)
                balances = self.store._balances(db, key.scope)
                row, _ = self.store._call(db, key)
                task = balances[1][0]
                require(task['cancel_generation'] < 2147483647, 'LIMIT_EXCEEDED')
                generation = task['cancel_generation']+1
                db.execute(update(t.balance).where(t.balance.c.balance_id == balance_id(key.scope, 'task'))
                           .values(state='CANCELLED', cancel_generation=generation))
                db.execute(update(t.reservation).where(t.reservation.c.key_digest.in_(
                    select(t.core.c.key_digest).where(t.core.c.task_id == key.scope.task_id))).values(cancelled=True))
            ticket.committed()
        return make('CancelAck', key_digest=key.fingerprint(), cancellation_generation=generation, state='CANCELLED')
