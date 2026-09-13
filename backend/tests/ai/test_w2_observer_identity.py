"""Immutable producer identities survive SQL copy ordering and journal restart."""
from contextlib import nullcontext

import pytest
from sqlalchemy import select

from app.db.session import SessionLocal
from app.ai.proposals.adapter import Usage
from app.ai.w2 import schema as t
from app.ai.w2.fake_authority import FakeAuthority, Journal
from app.ai.w2.records import PortError, decode, make, usage_view
from tests.ai.test_w2_accounting import accepted
from tests.ai.test_w2_execution import balances
from tests.ai.test_w2_recovery import restarted
from tests.ai.w2_fixtures import (w2, rule_pair, knowledge_pair, subject_pair,
    two_intake_targets, zero_capabilities, KNOWN)  # noqa: F401


CHANGED = Usage('known', 1000, 100, 0, 0, 20, 1100)


def final(authority, key):
    return next(event for event in authority.events(key) if event.kind == 'FINAL_USAGE')


@pytest.mark.parametrize('copied', [False, True])
@pytest.mark.parametrize('restart', [False, True])
def test_t9_identical_delivery_keeps_original_envelope_and_single_posting(w2, copied, restart):
    rt = accepted(w2)
    original = final(w2.authority, rt.key)
    if copied:
        rt.reconcile()
    context = restarted(w2) if restart else nullcontext(w2.authority)
    with context as authority:
        before = authority.journal.path.read_bytes(), authority.witness.journal.path.read_bytes()
        w2.clock.advance(.25)
        assert authority.late_usage(rt.key, KNOWN, event_id=original.event_id) == original
        assert (authority.journal.path.read_bytes(), authority.witness.journal.path.read_bytes()) == before
        assert authority.coverage() and authority.witness.accepted[rt.key.fingerprint()] == 1
        result = w2.store.reconcile_v1(rt.run, rt.key, [original.event_id], authority)
        assert (result.settled_tokens, result.settled_microusd) == (2432, 8704)
        with SessionLocal() as db:
            assert len(list(db.execute(select(t.settlement)))) == 1
            assert len(list(db.execute(select(t.posting)))) == 4
            assert not list(db.execute(select(t.conflict)))
        assert all((b['settled_tokens'], b['settled_microusd'], b['held_tokens'], b['held_microusd'])
            == (2432, 8704, 0, 0) for b in balances(rt.key))


@pytest.mark.parametrize('copied', [False, True])
@pytest.mark.parametrize('restart', [False, True])
def test_t9_conflicting_delivery_retains_both_contents_and_requires_explicit_recovery(w2, copied, restart):
    rt = accepted(w2)
    original = final(w2.authority, rt.key)
    if copied:
        rt.reconcile()
    conflict = w2.authority.late_usage(rt.key, CHANGED, event_id=original.event_id)
    assert conflict.kind == 'CONFLICT'
    assert conflict.event_id != original.event_id
    assert conflict.evidence_digest == original.fingerprint()
    assert conflict.usage == usage_view(CHANGED)
    assert (conflict.key_digest, conflict.call_ref, conflict.permit_id, conflict.body_digest,
            conflict.owner_generation, conflict.terminal) == (
        original.key_digest, original.call_ref, original.permit_id, original.body_digest,
        original.owner_generation, original.terminal)
    context = restarted(w2) if restart else nullcontext(w2.authority)
    with context as authority:
        assert authority.state == 'RECOVERY_REQUIRED' and authority.coverage()
        assert final(authority, rt.key) == original
        assert authority.observations[conflict.event_id] == conflict
        logged = [row['payload'] for row in authority.journal.entries]
        assert logged.count(original.document()) == logged.count(conflict.document()) == 1
        assert authority.witness.accepted[rt.key.fingerprint()] == 1
        before = authority.journal.path.read_bytes(), authority.witness.journal.path.read_bytes()
        assert authority.late_usage(rt.key, CHANGED, event_id=original.event_id) == conflict
        assert authority.late_usage(rt.key, KNOWN, event_id=original.event_id) == original
        assert (authority.journal.path.read_bytes(), authority.witness.journal.path.read_bytes()) == before
        with pytest.raises(PortError):
            authority.qualify_admission()
        # Selecting only the original must not hide a producer-side conflict
        # that arrived before SQL copied either of the contradictory contents.
        with pytest.raises(PortError, match='CONFLICT'):
            w2.store.reconcile_v1(rt.run, rt.key, [original.event_id], authority)
        for balance in balances(rt.key):
            assert balance['state'] == 'PAUSED_UNKNOWN'
            assert balance['held_tokens'] >= 5120 and balance['held_microusd'] >= 22528
            assert (balance['settled_tokens'], balance['settled_microusd']) == ((2432, 8704) if copied else (0, 0))
        with SessionLocal() as db:
            row = db.execute(select(t.reservation)).mappings().one()
            assert row['state'] == 'CONFLICT'
            conflicts = list(db.execute(select(t.conflict)).mappings())
            assert len(conflicts) == 1 and conflicts[0]['reason'] == 'EVENT_CONFLICT'
        with pytest.raises(PortError):
            w2.recovery.resume_budgets(w2.read(), 'premature_resume', w2.deadline())
        recovery = w2.recovery.recover_call(w2.read(), rt.key, 'identity_review', 1,
            'identity_recovery_owner', w2.deadline())
        result = w2.store.reconcile_v1(recovery, rt.key, [original.event_id], authority,
            recovery_decision='identity_review')
        assert (result.settled_tokens, result.settled_microusd) == (2432, 8704)
        assert all(b['state'] == 'PAUSED_UNKNOWN' and b['held_tokens'] == b['held_microusd'] == 0
            for b in balances(rt.key))
        with SessionLocal() as db:
            original_copy = db.scalar(select(t.observation_copy.c.record)
                .where(t.observation_copy.c.event_id == original.event_id))
            assert decode('Observation', bytes(original_copy)) == original
            assert len(list(db.execute(select(t.conflict)))) == 1
            assert not list(db.execute(select(t.completion)))
        assert authority.state == 'CLOSED'
        with pytest.raises(PortError):
            authority.consume_write_v1(rt.permit, rt.prepared.body)
        assert authority.witness.accepted[rt.key.fingerprint()] == 1


def test_t9_identical_totals_do_not_make_changed_terminal_idempotent(w2):
    rt = accepted(w2)
    original = final(w2.authority, rt.key)
    conflict = w2.authority.late_usage(rt.key, KNOWN, terminal='FAILED', event_id=original.event_id)
    assert conflict.kind == 'CONFLICT' and conflict.event_id != original.event_id
    assert conflict.terminal == 'FAILED' and conflict.usage == original.usage
    assert conflict.evidence_digest == original.fingerprint()
    assert final(w2.authority, rt.key).terminal == 'COMPLETED'
    with pytest.raises(PortError, match='CONFLICT'):
        w2.store.reconcile_v1(rt.run, rt.key, [original.event_id], w2.authority)
    assert all(b['state'] == 'PAUSED_UNKNOWN' and b['held_tokens'] == 5120 for b in balances(rt.key))


def test_t9_reviewer_control_flow_copies_rejected_identity_and_pauses_both_budgets(w2):
    rt = accepted(w2)
    original = final(w2.authority, rt.key)
    # Preserve the reviewer's try/producer/reconcile/except ordering. A
    # producer exception here would skip reconciliation and leave SQL ACTIVE.
    try:
        incoming = w2.authority.late_usage(rt.key, CHANGED, event_id=original.event_id)
        rt.reconcile()
    except PortError:
        pass
    assert incoming.kind == 'CONFLICT' and incoming.event_id != original.event_id
    assert incoming.evidence_digest == original.fingerprint()
    assert incoming.usage == usage_view(CHANGED)
    assert w2.authority.observations[original.event_id] == original
    assert original.usage == usage_view(KNOWN)
    assert w2.authority.coverage() and w2.authority.state == 'RECOVERY_REQUIRED'
    for balance in balances(rt.key):
        assert balance['state'] == 'PAUSED_UNKNOWN'
        assert balance['held_tokens'] >= 5120 and balance['held_microusd'] >= 22528
        assert (balance['settled_tokens'], balance['settled_microusd']) == (0, 0)
    with SessionLocal() as db:
        reservation = db.execute(select(t.reservation)).mappings().one()
        assert reservation['state'] == 'CONFLICT' and reservation['state'] != 'SETTLED'
        copies = {r.event_id: decode('Observation', bytes(r.record))
            for r in db.execute(select(t.observation_copy.c.event_id, t.observation_copy.c.record))}
        assert copies[original.event_id] == original
        assert copies[incoming.event_id] == incoming
        assert len(list(db.execute(select(t.conflict)))) == 1
        assert not list(db.execute(select(t.settlement)))
    assert w2.witness.accepted[rt.key.fingerprint()] == 1


def test_t9_new_id_contradiction_closes_admission_before_sql_copy(w2):
    rt = accepted(w2)
    rt.reconcile()
    rt.finish(rt.receipt)
    original = final(w2.authority, rt.key)
    assert w2.authority.state == 'OPEN'
    with SessionLocal() as db:
        admissions = list(db.execute(select(t.admission)).mappings())
        assert len(admissions) == 1 and admissions[0]['closed']
    incoming = w2.authority.late_usage(rt.key, CHANGED)
    assert incoming.kind == 'FINAL_USAGE' and incoming.event_id != original.event_id
    assert w2.authority.state == 'RECOVERY_REQUIRED' and w2.authority.coverage()
    # The producer's durable authority stops acceptance immediately, even
    # while both SQL balances still reflect the previously settled final.
    assert all(b['state'] == 'ACTIVE' and b['held_tokens'] == 0 for b in balances(rt.key))
    with pytest.raises(PortError, match='OBSERVER_UNAVAILABLE'):
        w2.authority.qualify_admission()
    with SessionLocal() as db:
        assert db.scalar(select(t.observation_copy.c.event_id)
            .where(t.observation_copy.c.event_id == incoming.event_id)) is None
        assert list(db.execute(select(t.admission)).mappings()) == admissions
    assert w2.witness.accepted[rt.key.fingerprint()] == 1
    with pytest.raises(PortError, match='CONFLICT'):
        rt.reconcile()
    assert all(b['state'] == 'PAUSED_UNKNOWN' and b['held_tokens'] >= 5120 for b in balances(rt.key))
    recovery = w2.recovery.recover_call(w2.read(), rt.key, 'fresh_id_review', 1,
        'fresh_id_recovery_owner', w2.deadline())
    result = w2.store.reconcile_v1(recovery, rt.key, [incoming.event_id], w2.authority,
        recovery_decision='fresh_id_review')
    assert (result.settled_tokens, result.settled_microusd) == (1100, 3200)
    assert all(b['state'] == 'PAUSED_UNKNOWN' and b['held_tokens'] == b['held_microusd'] == 0
        for b in balances(rt.key))
    assert w2.authority.state == 'CLOSED'
    with pytest.raises(PortError):
        w2.authority.consume_write_v1(rt.permit, rt.prepared.body)
    with SessionLocal() as db:
        assert list(db.execute(select(t.admission)).mappings()) == admissions
        assert not list(db.execute(select(t.completion)))
    assert w2.witness.accepted[rt.key.fingerprint()] == 1


def test_t9_durable_observer_capacity_rejection_stops_admission_without_eviction(w2):
    rt = accepted(w2)
    rt.reconcile()
    rt.finish(rt.receipt)
    while w2.authority.calls[rt.key.fingerprint()]['events'] < 56:
        w2.authority.late_usage(rt.key, KNOWN)
    assert w2.authority.state == 'OPEN'
    original = w2.authority.events(rt.key)
    before = w2.journal.path.read_bytes(), w2.witness.journal.path.read_bytes()
    with pytest.raises(PortError, match='LIMIT_EXCEEDED'):
        w2.authority.late_usage(rt.key, KNOWN)
    assert w2.authority.state == 'RECOVERY_REQUIRED' and w2.authority.coverage()
    assert w2.authority.events(rt.key) == original and len(original) == 56
    assert (w2.journal.path.read_bytes(), w2.witness.journal.path.read_bytes()) == before
    with pytest.raises(PortError, match='OBSERVER_UNAVAILABLE'):
        w2.authority.qualify_admission()
    with restarted(w2) as authority:
        assert authority.coverage() and authority.events(rt.key) == original
        assert authority.state == 'RECOVERY_REQUIRED'
        with pytest.raises(PortError, match='OBSERVER_UNAVAILABLE'):
            authority.qualify_admission()
        assert authority.witness.accepted[rt.key.fingerprint()] == 1
    with SessionLocal() as db:
        admissions = list(db.execute(select(t.admission)).mappings())
        assert len(admissions) == 1 and admissions[0]['closed']


@pytest.mark.parametrize('boundary', ['after_fsync', 'before_ack'])
def test_t9_conflict_ack_loss_preserves_original_and_durable_candidate(w2, boundary):
    rt = accepted(w2)
    original = final(w2.authority, rt.key)

    def lose_ack(stage):
        if stage == boundary:
            raise PortError('COMMIT_UNKNOWN')

    w2.authority.hook = lose_ack
    with pytest.raises(PortError, match='COMMIT_UNKNOWN'):
        w2.authority.late_usage(rt.key, CHANGED, event_id=original.event_id)
    w2.authority.hook = lambda _: None
    assert w2.authority.observations[original.event_id] == original
    assert not w2.authority.coverage()
    with restarted(w2) as authority:
        conflict = next(e for e in authority.events(rt.key) if e.kind == 'CONFLICT')
        assert final(authority, rt.key) == original
        assert conflict.usage == usage_view(CHANGED)
        assert conflict.evidence_digest == original.fingerprint()
        assert authority.state == 'RECOVERY_REQUIRED'
        assert authority.coverage() is (boundary == 'before_ack')
        assert authority.witness.accepted[rt.key.fingerprint()] == 1
        with pytest.raises(PortError):
            w2.store.reconcile_v1(rt.run, rt.key, [original.event_id], authority)
        with pytest.raises(PortError):
            authority.qualify_admission()
        assert all(b['held_tokens'] >= 5120 and b['held_microusd'] >= 22528
            for b in balances(rt.key))


def test_t9_legacy_duplicate_journal_is_not_collapsed_during_coverage_or_restart(w2):
    rt = accepted(w2)
    original = final(w2.authority, rt.key)
    # Reproduce the prior producer's physically durable duplicate-ID history.
    # The administrative fixture owns these synthetic journals; it does not
    # alter SQL, endpoint acceptance or the original observation frame.
    duplicate = make('Observation', **{**original.document(),
        'sequence': len(w2.journal.entries) + 1, 'usage': usage_view(CHANGED),
        'evidence_digest': w2.journal.head})
    row = w2.journal.append(duplicate.document())
    w2.witness.head(row)
    w2.authority.observations[original.event_id] = duplicate
    assert not w2.authority.coverage()
    before = w2.journal.path.read_bytes()
    w2.journal.close()
    journal = Journal(w2.journal.path)
    try:
        with pytest.raises(PortError, match='OBSERVER_UNAVAILABLE'):
            FakeAuthority(w2.scope.deployment_ref, journal, w2.witness, w2.clock)
        assert journal.path.read_bytes() == before
        payloads = [r['payload'] for r in journal.entries]
        assert original.document() in payloads and duplicate.document() in payloads
    finally:
        journal.close()
    assert all(b['held_tokens'] == 5120 and b['held_microusd'] == 22528 for b in balances(rt.key))
