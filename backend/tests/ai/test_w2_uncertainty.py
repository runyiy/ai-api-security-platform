"""T4/T9: evidence order cannot erase uncertainty or authorize resumption."""
import pytest
from sqlalchemy import insert, select, text

from app.db.session import SessionLocal, engine
from app.ai.proposals.adapter import Usage
from app.ai.w2 import schema as t
from app.ai.w2.records import MAX_N, PortError, decode
from tests.ai.test_w2_accounting import accepted
from tests.ai.test_w2_execution import balances
from tests.ai.test_w2_recovery import restarted
from tests.ai.w2_fixtures import (w2, rule_pair, knowledge_pair, subject_pair,
    two_intake_targets, zero_capabilities, KNOWN)  # noqa: F401


OVERFLOW = Usage('known', MAX_N, 0, 0, 0, 0, MAX_N)
CHANGED = Usage('known', 1000, 100, 0, 0, 20, 1100)


def snapshot(key):
    with SessionLocal() as db:
        row = dict(db.execute(select(t.reservation).where(t.reservation.c.key_digest == key.fingerprint())).mappings().one())
        copies = [decode('Observation', bytes(raw)) for raw in db.scalars(select(t.observation_copy.c.record))]
        events = list(db.execute(select(t.event)).mappings())
        postings = list(db.execute(select(t.posting)).mappings())
    return row, copies, events, postings


def frozen(key, settled):
    row, copies, events, postings = snapshot(key)
    assert row['state'] == 'CONFLICT'
    assert (row['actual_tokens'], row['actual_microusd']) == settled
    assert row['held_tokens'] >= 5120 and row['held_microusd'] >= 22528
    for balance in balances(key):
        assert balance['state'] == 'PAUSED_UNKNOWN'
        assert (balance['settled_tokens'], balance['settled_microusd']) == settled
        assert (balance['held_tokens'], balance['held_microusd']) == (row['held_tokens'], row['held_microusd'])
    return row, copies, events, postings


@pytest.mark.parametrize('overflow_first', [False, True])
@pytest.mark.parametrize('separate_batches', [False, True])
@pytest.mark.parametrize('cleanup', [False, True])
def test_t9_overflow_and_known_order_cannot_erase_conflict(w2, overflow_first, separate_batches, cleanup):
    rt = accepted(w2, OVERFLOW if overflow_first else KNOWN)
    settled = (0, 0)
    if separate_batches:
        if overflow_first:
            with pytest.raises(PortError, match='CONFLICT'):rt.reconcile()
        else:
            assert rt.reconcile().state == 'KNOWN'
            settled = (2432, 8704)
    w2.authority.late_usage(rt.key, KNOWN if overflow_first else OVERFLOW)
    with pytest.raises(PortError, match='CONFLICT'):rt.reconcile()
    before = frozen(rt.key, settled)
    assert {e.usage.input_tokens for e in before[1] if e.kind == 'FINAL_USAGE'} == {2048, MAX_N}
    if cleanup:
        w2.store.mark_unknown(rt.run, rt.key)
        assert snapshot(rt.key)[0]['state'] == 'CONFLICT'
    with pytest.raises(PortError, match='CONFLICT'):rt.reconcile()
    assert frozen(rt.key, settled) == before
    with pytest.raises(PortError):w2.recovery.resume_budgets(w2.read(), 'cannot_resume', w2.deadline())
    with pytest.raises(PortError):w2.runtime('Q4b')
    assert w2.witness.accepted[rt.key.fingerprint()] == 1


@pytest.mark.parametrize('unknown_first', [False, True])
@pytest.mark.parametrize('separate_batches', [False, True])
def test_t9_unknown_order_returns_current_disposition_and_holds_liability(w2, unknown_first, separate_batches):
    rt = accepted(w2, Usage() if unknown_first else KNOWN)
    if unknown_first:
        # An unknown scripted adapter outcome omits final evidence entirely;
        # this schedule needs an actual independently recorded unknown final.
        w2.authority.late_usage(rt.key, Usage())
    expected = (0, 0)
    if separate_batches:
        first = rt.reconcile()
        if not unknown_first:
            assert first.state == 'KNOWN'
            expected = (2432, 8704)
    w2.authority.late_usage(rt.key, KNOWN if unknown_first else Usage())
    result = rt.reconcile()
    row = snapshot(rt.key)[0]
    assert result.state == 'UNKNOWN' and row['state'] == 'IN_DOUBT'
    assert (result.settled_tokens, result.settled_microusd) == expected
    assert (result.held_tokens, result.held_microusd) == (row['held_tokens'], row['held_microusd'])
    assert result.held_tokens >= 5120 and result.held_microusd >= 22528
    assert all(b['state'] == 'PAUSED_UNKNOWN' for b in balances(rt.key))
    with pytest.raises(PortError):w2.recovery.resume_budgets(w2.read(), 'unknown_resume', w2.deadline())


def test_t9_conflict_and_following_overcap_retain_all_selected_evidence(w2):
    rt = accepted(w2)
    rt.reconcile()
    w2.authority.late_usage(rt.key, CHANGED)
    high = w2.authority.late_usage(rt.key, Usage('known', 30000, 5000, 0, 0, 100, 35000))
    with pytest.raises(PortError, match='CONFLICT'):rt.reconcile()
    row, copies, _, _ = frozen(rt.key, (2432, 8704))
    assert high in copies
    assert row['actual_tokens']+row['held_tokens'] >= 35000
    assert row['actual_microusd']+row['held_microusd'] >= 120000


@pytest.mark.parametrize('large_first', [False, True])
def test_t9_before_first_copy_every_known_lower_bound_remains_held(w2, large_first):
    high = Usage('known', 30000, 5000, 0, 0, 100, 35000)
    rt = accepted(w2, high if large_first else KNOWN)
    w2.authority.late_usage(rt.key, KNOWN if large_first else high)
    with pytest.raises(PortError, match='CONFLICT'):rt.reconcile()
    row, copies, _, _ = frozen(rt.key, (0, 0))
    assert row['held_tokens'] >= 35000 and row['held_microusd'] >= 120000
    assert {e.usage.total_tokens for e in copies if e.kind == 'FINAL_USAGE'} == {2432, 35000}


def test_t9_unknown_after_settlement_can_be_explicitly_resolved_without_new_totals(w2):
    rt = accepted(w2)
    original = next(e for e in w2.authority.events(rt.key) if e.kind == 'FINAL_USAGE')
    first = rt.reconcile()
    w2.authority.late_usage(rt.key, Usage())
    ctx = w2.recovery.recover_call(w2.read(), rt.key, 'same_amount_review', 1, 'reviewer', w2.deadline())
    settled = w2.store.reconcile_v1(ctx, rt.key, [original.event_id], w2.authority)
    assert settled.state == 'KNOWN' and settled.revision == first.revision+1
    with SessionLocal() as db:
        assert db.scalar(select(t.event.c.kind).where(t.event.c.kind == 'RECOVERY_RESOLVED')) == 'RECOVERY_RESOLVED'
        assert all(p.settled_delta == 0 for p in db.execute(select(t.posting)
            .where(t.posting.c.settlement_id == settled.settlement_id)))
    w2.recovery.resume_budgets(w2.read(), 'same_amount_resume', w2.deadline())
    assert all(b['state'] == 'ACTIVE' for b in balances(rt.key))


def test_t9_coverage_loss_after_settlement_retains_known_spend_and_freezes(w2):
    rt = accepted(w2)
    first = rt.reconcile()
    before = snapshot(rt.key)[3]
    accepted_event = next(e for e in w2.authority.events(rt.key) if e.kind == 'WRITE_ACCEPTED')
    del w2.authority.observations[accepted_event.event_id]
    assert not w2.authority.coverage()
    result = rt.reconcile()
    assert result.state == 'UNKNOWN'
    assert (result.settled_tokens, result.settled_microusd) == (first.settled_tokens, first.settled_microusd)
    assert result.held_tokens >= 5120 and result.held_microusd >= 22528
    assert snapshot(rt.key)[3] == before
    assert all(b['state'] == 'PAUSED_UNKNOWN' for b in balances(rt.key))
    with pytest.raises(PortError):w2.recovery.resume_budgets(w2.read(), 'coverage_resume', w2.deadline())


@pytest.mark.parametrize('error', ['OBSERVER_UNAVAILABLE', 'DEADLINE_EXCEEDED'])
def test_t9_direct_reconcile_ack_failure_preserves_spend_and_pauses(w2, monkeypatch, error):
    rt = accepted(w2)
    rt.reconcile()
    original = next(e for e in w2.authority.events(rt.key) if e.kind == 'FINAL_USAGE')
    def unavailable(*args):raise PortError(error)
    monkeypatch.setattr(w2.authority, 'observe_v1', unavailable)
    with pytest.raises(PortError, match=error):
        w2.store.reconcile_v1(rt.run, rt.key, [original.event_id], w2.authority)
    current = w2.store.current_settlement(rt.key)
    assert current.state == 'UNKNOWN'
    assert (current.settled_tokens, current.settled_microusd, current.held_tokens, current.held_microusd) == (2432, 8704, 5120, 22528)
    assert all(b['state'] == 'PAUSED_UNKNOWN' for b in balances(rt.key))


def test_t9_observation_id_collision_with_ledger_event_commits_conflict(w2):
    rt = accepted(w2, Usage())
    with SessionLocal() as db:
        original = dict(db.execute(select(t.event).where(t.event.c.kind == 'RESERVED')).mappings().one())
    incoming = w2.authority.late_usage(rt.key, KNOWN, event_id=original['event_id'])
    with pytest.raises(PortError, match='CONFLICT'):rt.reconcile()
    frozen(rt.key, (0, 0))
    with SessionLocal() as db:
        assert dict(db.execute(select(t.event).where(t.event.c.event_id == original['event_id'])).mappings().one()) == original
        assert db.scalar(select(t.conflict.c.reason)) == 'EVENT_CONFLICT'
    assert incoming in w2.authority.events(rt.key) and w2.authority.coverage()


@pytest.mark.parametrize('settled_before', [False, True])
def test_t9_coverage_failure_after_observer_ack_is_sticky_within_batch(w2, monkeypatch, settled_before):
    rt = accepted(w2)
    if settled_before:rt.reconcile()
    w2.authority.late_usage(rt.key, KNOWN)
    observe, coverage = w2.authority.observe_v1, w2.authority.coverage
    acknowledgements, failures = [], []
    total = len(w2.authority.events(rt.key))

    def acknowledge(*args):
        result = observe(*args)
        acknowledgements.append(result)
        return result

    def lose_coverage_once():
        if len(acknowledgements) == total and not failures:
            failures.append('after_independent_ack_before_settlement')
            return False
        return coverage()

    monkeypatch.setattr(w2.authority, 'observe_v1', acknowledge)
    monkeypatch.setattr(w2.authority, 'coverage', lose_coverage_once)
    with pytest.raises(PortError, match='CONFLICT'):rt.reconcile()
    assert len(acknowledgements) == total and len(failures) == 1
    frozen(rt.key, (2432, 8704) if settled_before else (0, 0))
    with pytest.raises(PortError, match='CONFLICT'):rt.reconcile()


@pytest.mark.parametrize('when', ['during_ack', 'after_settlement_commit'])
def test_t9_new_independent_conflict_during_reconciliation_cannot_authorize_refund(w2, monkeypatch, when):
    rt = accepted(w2)
    original = next(e for e in w2.authority.events(rt.key) if e.kind == 'FINAL_USAGE')
    fired = []

    def conflict():
        if not fired:
            fired.append(True)
            event = w2.authority.late_usage(rt.key, CHANGED, event_id=original.event_id)
            assert event.kind == 'CONFLICT' and event.event_id != original.event_id
            assert event.evidence_digest == original.fingerprint()

    if when == 'during_ack':
        observe = w2.authority.observe_v1
        def acknowledge(*args):
            value = observe(*args)
            conflict()
            return value
        monkeypatch.setattr(w2.authority, 'observe_v1', acknowledge)
    else:
        w2.store.hook = lambda stage: conflict() if stage == 'after_commit' else None
    result = rt.reconcile()
    w2.store.hook = lambda _: None
    assert fired and result.state == 'UNKNOWN'
    assert result.held_tokens >= 5120 and result.held_microusd >= 22528
    assert all(b['state'] == 'PAUSED_UNKNOWN' for b in balances(rt.key))
    assert w2.authority.coverage() and w2.authority.state == 'RECOVERY_REQUIRED'
    assert w2.witness.accepted[rt.key.fingerprint()] == 1
    with pytest.raises(PortError, match='CONFLICT'):rt.reconcile()


def test_t9_recovery_retires_exact_prefix_and_cannot_resolve_later_evidence(w2):
    rt = accepted(w2, OVERFLOW)
    chosen = w2.authority.late_usage(rt.key, KNOWN)
    with pytest.raises(PortError):rt.reconcile()
    ctx = w2.recovery.recover_call(w2.read(), rt.key, 'overflow_review', 1, 'reviewer', w2.deadline())
    with pytest.raises(PortError, match='CONFLICT'):
        w2.store.reconcile_v1(ctx, rt.key, [chosen.event_id], w2.authority)
    result = w2.store.reconcile_v1(ctx, rt.key, [chosen.event_id], w2.authority, recovery_decision='overflow_review')
    assert result.state == 'KNOWN' and result.settled_tokens == 2432
    before = snapshot(rt.key)
    # Rejected overflow and original IDs remain immutable history. Replaying
    # them under the current owner neither reapplies nor recreates the dispute.
    for event in w2.authority.events(rt.key):
        assert w2.store.reconcile_v1(ctx, rt.key, [event.event_id], w2.authority) == result
    assert snapshot(rt.key) == before
    assert all(b['state'] == 'PAUSED_UNKNOWN' and b['held_tokens'] == 0 for b in balances(rt.key))
    w2.recovery.resume_budgets(w2.read(), 'reviewed_resume', w2.deadline())
    assert all(b['state'] == 'ACTIVE' for b in balances(rt.key))
    assert w2.authority.state == 'CLOSED'
    later = w2.authority.late_usage(rt.key, CHANGED)
    with pytest.raises(PortError, match='CONFLICT'):
        w2.store.reconcile_v1(ctx, rt.key, [later.event_id], w2.authority, recovery_decision='overflow_review')
    frozen(rt.key, (2432, 8704))
    second = w2.recovery.recover_call(w2.read(), rt.key, 'later_review', 2, 'second_reviewer', w2.deadline())
    corrected = w2.store.reconcile_v1(second, rt.key, [later.event_id], w2.authority, recovery_decision='later_review')
    assert corrected.revision == 2 and (corrected.settled_tokens, corrected.settled_microusd) == (1100, 3200)
    assert w2.witness.accepted[rt.key.fingerprint()] == 1


def test_t9_new_core_conflict_needs_new_decision_even_after_observer_prefix_resolution(w2):
    rt = accepted(w2)
    rt.reconcile()
    chosen = w2.authority.late_usage(rt.key, CHANGED)
    with pytest.raises(PortError):rt.reconcile()
    first = w2.recovery.recover_call(w2.read(), rt.key, 'first_core_review', 1, 'reviewer', w2.deadline())
    settled = w2.store.reconcile_v1(first, rt.key, [chosen.event_id], w2.authority, recovery_decision='first_core_review')
    w2.recovery.resume_budgets(w2.read(), 'core_resume', w2.deadline())
    w2.reopen()
    with pytest.raises(PortError, match='CONFLICT'):w2.runtime('Q4')
    frozen(rt.key, (1100, 3200))
    with pytest.raises(PortError, match='CONFLICT'):
        w2.store.reconcile_v1(first, rt.key, [chosen.event_id], w2.authority, recovery_decision='first_core_review')
    second = w2.recovery.recover_call(w2.read(), rt.key, 'second_core_review', 2, 'second_reviewer', w2.deadline())
    result = w2.store.reconcile_v1(second, rt.key, [chosen.event_id], w2.authority, recovery_decision='second_core_review')
    assert result.state == 'KNOWN' and result.revision == settled.revision+1
    assert (result.settled_tokens, result.settled_microusd, result.held_tokens, result.held_microusd) == (1100, 3200, 0, 0)
    with SessionLocal() as db:
        assert db.scalar(select(t.conflict.c.reason).where(t.conflict.c.reason == 'CORE_CONFLICT')) == 'CORE_CONFLICT'
        assert all(p.settled_delta == 0 for p in db.execute(select(t.posting)
            .where(t.posting.c.settlement_id == result.settlement_id)))
    assert w2.witness.accepted[rt.key.fingerprint()] == 1


@pytest.mark.parametrize('omit_resolution', [False, True])
def test_t11_restart_and_stale_sql_cannot_hide_earlier_overflow(w2, omit_resolution):
    rt = accepted(w2, OVERFLOW)
    chosen = w2.authority.late_usage(rt.key, KNOWN)
    with pytest.raises(PortError):rt.reconcile()
    ctx = w2.recovery.recover_call(w2.read(), rt.key, 'restart_review', 1, 'reviewer', w2.deadline())
    result = w2.store.reconcile_v1(ctx, rt.key, [chosen.event_id], w2.authority, recovery_decision='restart_review')
    assert result.state == 'KNOWN'
    if omit_resolution:
        # This run owns the synthetic DB. Restore an older event projection
        # while keeping settlement and independent journals unchanged.
        with engine.begin() as db:
            events = [dict(r) for r in db.execute(select(t.event)).mappings() if r['kind'] != 'RECOVERY_RESOLVED']
            db.execute(text('TRUNCATE research_ai_event'))
            db.execute(insert(t.event), events)
    with restarted(w2) as authority:
        assert authority.coverage() and authority.witness.accepted[rt.key.fingerprint()] == 1
        if omit_resolution:
            with pytest.raises(PortError):w2.recovery.resume_budgets(w2.read(), 'restore_resume', w2.deadline())
            assert all(b['state'] == 'PAUSED_UNKNOWN' for b in balances(rt.key))
        else:
            w2.recovery.resume_budgets(w2.read(), 'restore_resume', w2.deadline())
            assert all(b['state'] == 'ACTIVE' for b in balances(rt.key))
        assert authority.state == 'CLOSED'
        with pytest.raises(PortError):authority.consume_write_v1(rt.permit, rt.prepared.body)
