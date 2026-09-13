"""Volatile exact-result reuse preserves independently observed W2 accounting."""
from datetime import timedelta
import json

import pytest
from sqlalchemy import select, text

from app.db.session import SessionLocal
from app.ai.proposals.adapter import Usage
from app.ai.proposals.codec import canonical
from app.ai.w2 import schema as t
from app.ai.w2.execution import execute_fake
from app.ai.w2.records import PortError, decode, make, stamp, usage_view, utc
from app.ai.w2.registry import ref_id
from app.ai.w3.cache import AnalysisCache, MAX_ENTRIES
from app.services import research_context
from tests.ai.test_w2_execution import balances, response
from tests.research_rule_fixtures import call, REF
from tests.ai.w2_fixtures import (w2, rule_pair, knowledge_pair, subject_pair,
    two_intake_targets, zero_capabilities, KNOWN)  # noqa: F401


def sql_state():
    """Fresh session reads every W2 row, including immutable event bytes."""
    with SessionLocal() as db:
        return {table.name: tuple(dict(row) for row in db.execute(
            select(table).order_by(*table.primary_key.columns)).mappings())
            for table in t.TABLES}


def independent_state(w2):
    return (w2.journal.path.read_bytes(), w2.witness.journal.path.read_bytes(),
            dict(w2.witness.accepted), dict(w2.authority.endpoint_bodies))


def read(cache, w2, runtime):
    context = w2.read()
    return cache.read_v1(runtime, context, w2.deadline(context))


def completed(w2, tmp_path, mode='success'):
    w2.reopen()
    runtime = w2.runtime()
    raw = response(runtime)
    if mode == 'failed':
        raw = response(runtime, change={'output': []})
    elif mode == 'refusal':
        envelope = json.loads(raw.partition(b'\r\n\r\n')[2])
        proposal = dict(protocol='ra-ai-proposal-output/1', request_ref=runtime.key.call_ref,
                        status='refusal', suggestions=[], refusal_code='SAFETY_REFUSAL')
        envelope['output'][0]['content'][0]['text'] = canonical(proposal).decode()
        body = canonical(envelope)
        raw = (b'HTTP/1.1 200 Synthetic\r\nContent-Type: application/json\r\nContent-Length: '
               + str(len(body)).encode() + b'\r\n\r\n' + body)
    elif mode == 'unknown':
        runtime.hook = lambda stage: w2.authority.script_final(runtime.key, Usage()) if stage == 'admitted' else None
        raw = response(runtime, usage=Usage())
    completion, outcome = execute_fake(runtime, raw, owned_directory=tmp_path)
    assert w2.witness.accepted[runtime.key.fingerprint()] == 1
    assert w2.authority.endpoint_bodies == {runtime.key.fingerprint(): runtime.prepared.body}
    assert w2.authority.coverage()
    return runtime, completion, outcome


def remembered(w2, tmp_path):
    runtime, completion, outcome = completed(w2, tmp_path)
    assert completion.display_state == 'ELIGIBLE_NOW' and outcome.code is None
    cache = AnalysisCache()
    cache.remember_v1(runtime, completion, outcome)
    return cache, runtime, completion, outcome


def test_qualified_miss_then_repeated_hits_leave_every_ledger_row_and_witness_unchanged(w2, tmp_path):
    w2.reopen()
    runtime = w2.runtime(reserve=False)
    cache = AnalysisCache()
    before = sql_state(), independent_state(w2)
    assert read(cache, w2, runtime) is None
    assert (sql_state(), independent_state(w2)) == before
    assert not before[0][t.core.name] and not before[0][t.reservation.name]
    assert not w2.authority.endpoint_bodies and not w2.authority.calls

    runtime.reserve_v1()
    completion, outcome = execute_fake(runtime, response(runtime), owned_directory=tmp_path)
    assert outcome.code is None and outcome.usage == KNOWN
    assert completion.display_state == 'ELIGIBLE_NOW'
    before = sql_state(), independent_state(w2)
    key = cache.remember_v1(runtime, completion, outcome)
    assert cache.remember_v1(runtime, completion, outcome) == key
    for _ in range(2):
        w2.clock.advance(.1)
        hit = read(cache, w2, runtime)
        assert hit is not None and hit.key_digest == key
        assert hit.display == outcome.display
        assert hit.origin_call_ref == runtime.key.call_ref
        assert hit.origin_completion_id == completion.completion_id
        assert hit.origin_settlement_id == completion.settlement_ref
        assert hit.origin_usage == usage_view(KNOWN)
        with pytest.raises((AttributeError, TypeError)):
            hit.display = ()
        assert (sql_state(), independent_state(w2)) == before
    assert w2.authority.endpoint_bodies == {runtime.key.fingerprint(): runtime.prepared.body}
    assert w2.witness.accepted[runtime.key.fingerprint()] == 1 and w2.authority.coverage()
    for balance in balances(runtime.key):
        assert (balance['calls'], balance['settled_tokens'], balance['settled_microusd'],
                balance['held_tokens'], balance['held_microusd']) == (1, 2432, 8704, 0, 0)
    assert len(before[0][t.posting.name]) == 4 and len(before[0][t.completion.name]) == 1


def test_new_volatile_cache_misses_completed_call_without_automatic_replay(w2, tmp_path):
    cache, runtime, _, _ = remembered(w2, tmp_path)
    assert read(cache, w2, runtime) is not None
    before = sql_state(), independent_state(w2)
    assert read(AnalysisCache(), w2, runtime) is None
    assert (sql_state(), independent_state(w2)) == before
    assert w2.witness.accepted[runtime.key.fingerprint()] == 1


@pytest.mark.parametrize('mode', ['failed', 'refusal', 'unknown'])
def test_failed_refused_or_unknown_result_cannot_be_remembered_or_change_accounting(w2, tmp_path, mode):
    runtime, completion, outcome = completed(w2, tmp_path, mode)
    assert outcome.code is not None and not outcome.display
    if mode == 'refusal':
        assert outcome.refusal_code == 'SAFETY_REFUSAL'
    before = sql_state(), independent_state(w2)
    with pytest.raises(PortError):
        AnalysisCache().remember_v1(runtime, completion, outcome)
    assert (sql_state(), independent_state(w2)) == before
    for balance in balances(runtime.key):
        assert balance['calls'] == 1
        if mode == 'unknown':
            assert outcome.usage.state == 'unknown'
            assert balance['state'] == 'PAUSED_UNKNOWN'
            assert (balance['settled_tokens'], balance['settled_microusd'],
                    balance['held_tokens'], balance['held_microusd']) == (0, 0, 5120, 22528)
        else:
            assert outcome.usage == KNOWN
            assert (balance['settled_tokens'], balance['settled_microusd'],
                    balance['held_tokens'], balance['held_microusd']) == (2432, 8704, 0, 0)


def test_durable_cancellation_denies_hit_without_resuming_or_recharging(w2, tmp_path):
    cache, runtime, _, _ = remembered(w2, tmp_path)
    class Cancellation:
        def __init__(self):
            self.tasks = set()

        def set(self, task):
            self.tasks.add(task)

    cancellation = Cancellation()
    w2.recovery.cancel_v1(w2.read(), runtime.key, 'cache_cancel', w2.deadline(), cancellation)
    assert cancellation.tasks == {runtime.key.scope.task_id}
    before = sql_state(), independent_state(w2)
    with pytest.raises(PortError):
        read(cache, w2, runtime)
    assert (sql_state(), independent_state(w2)) == before
    assert w2.authority.state == 'CLOSED' and w2.witness.accepted[runtime.key.fingerprint()] == 1
    assert any(balance['state'] == 'CANCELLED' for balance in balances(runtime.key))
    assert all((balance['calls'], balance['settled_tokens'], balance['settled_microusd']) == (1, 2432, 8704)
               for balance in balances(runtime.key))


def test_late_conflict_denies_hits_before_and_after_sql_copy_and_retains_liability(w2, tmp_path):
    cache, runtime, _, _ = remembered(w2, tmp_path)
    original = next(event for event in w2.authority.events(runtime.key) if event.kind == 'FINAL_USAGE')
    conflict = w2.authority.late_usage(runtime.key, Usage('known', 1000, 100, 0, 0, 20, 1100),
                                       event_id=original.event_id)
    assert conflict.kind == 'CONFLICT' and conflict.evidence_digest == original.fingerprint()
    assert w2.authority.state == 'RECOVERY_REQUIRED' and w2.authority.coverage()
    before = sql_state(), independent_state(w2)
    assert not before[0][t.conflict.name]
    with pytest.raises(PortError):
        read(cache, w2, runtime)
    assert (sql_state(), independent_state(w2)) == before

    with pytest.raises(PortError, match='CONFLICT'):
        w2.store.reconcile_v1(runtime.run, runtime.key, [original.event_id], w2.authority)
    before = sql_state(), independent_state(w2)
    assert len(before[0][t.conflict.name]) == 1
    with pytest.raises(PortError):
        read(cache, w2, runtime)
    assert (sql_state(), independent_state(w2)) == before
    assert w2.authority.observations[original.event_id] == original
    assert w2.authority.observations[conflict.event_id] == conflict
    assert w2.witness.accepted[runtime.key.fingerprint()] == 1
    for balance in balances(runtime.key):
        assert balance['state'] == 'PAUSED_UNKNOWN' and balance['calls'] == 1
        assert (balance['settled_tokens'], balance['settled_microusd']) == (2432, 8704)
        assert balance['held_tokens'] >= 5120 and balance['held_microusd'] >= 22528


def test_fresh_read_deadline_cannot_extend_original_expiry_after_completed_lookup(w2, tmp_path, monkeypatch):
    cache, runtime, _, _ = remembered(w2, tmp_path)
    w2.clock.advance(.5)
    context = w2.read()
    deadline = w2.deadline(context)
    assert utc(context.deadline_at) > utc(runtime.prepared.core.expires_at)
    original = w2.preparation.requalify
    completed_reads = []
    def expire_after_lookup(*args, **kwargs):
        result = original(*args, **kwargs)
        completed_reads.append(result)
        w2.clock.advance((utc(runtime.prepared.core.expires_at) - w2.clock.utcnow()).total_seconds())
        return result

    monkeypatch.setattr(w2.preparation, 'requalify', expire_after_lookup)
    before = sql_state(), independent_state(w2)
    with pytest.raises(PortError):
        cache.read_v1(runtime, context, deadline)
    assert completed_reads
    assert (sql_state(), independent_state(w2)) == before
    assert w2.witness.accepted[runtime.key.fingerprint()] == 1


@pytest.mark.parametrize('change', ['expiry', 'context_close'])
def test_completed_g_release_requalifies_expiry_and_real_invalidation_before_consumption(w2, tmp_path, change):
    cache, runtime, _, _ = remembered(w2, tmp_path)
    before = sql_state(), independent_state(w2)
    completed_releases = []
    def after_release(stage, token):
        if stage != 'after_release':
            return
        assert token is None
        with SessionLocal() as db:
            assert db.scalar(text("SELECT count(*) FROM pg_locks WHERE locktype='advisory' "
                                  "AND classid=73105 AND objid=2 AND objsubid=2")) == 0
        if change == 'expiry':
            w2.clock.advance((utc(runtime.prepared.core.expires_at) - w2.clock.utcnow()).total_seconds())
        else:
            call(research_context.close_context, w2.g['ctx'],
                 dict(expected_version=1, closure_reference=REF), project=w2.g['project'])
            assert w2.authority.state == 'CLOSED'
        completed_releases.append((sql_state(), independent_state(w2)))

    cache.hook = after_release
    with pytest.raises(PortError):
        read(cache, w2, runtime)
    assert len(completed_releases) == 1
    assert (sql_state(), independent_state(w2)) == completed_releases[0]
    # A real lifecycle writer adds its own durable barrier. Cache consumption
    # cannot alter any other W2 row or the original endpoint acceptance.
    after = completed_releases[0]
    assert {name: rows for name, rows in after[0].items() if name != t.invalidation.name} == {
        name: rows for name, rows in before[0].items() if name != t.invalidation.name}
    assert after[1][2:] == before[1][2:]
    assert w2.witness.accepted[runtime.key.fingerprint()] == 1 and w2.authority.coverage()
    if change == 'context_close':
        assert len(after[0][t.invalidation.name]) == len(before[0][t.invalidation.name]) + 1
        assert after[1][0] != before[1][0] and after[1][1] != before[1][1]
    else:
        assert after == before


def test_versioned_candidate_correction_denies_original_cache_after_explicit_reopen(w2, tmp_path, monkeypatch):
    cache, runtime, _, _ = remembered(w2, tmp_path)
    original = runtime.prepared.core.candidates[0]
    entry_id = ref_id('candidate', w2.candidates[0])
    corrected = make('SyntheticCandidateCertificate', **{
        **original.document(), 'expires_at': stamp(utc(original.expires_at) - timedelta(seconds=1))})
    assert utc(corrected.expires_at) > utc(runtime.prepared.core.expires_at)
    operations = set(w2.authority.operations)
    w2.registry.install(entry_id, 2, corrected, w2.deadline())
    new_operations = set(w2.authority.operations) - operations
    assert len(new_operations) == 1 and w2.authority.state == 'CLOSED'
    operation_id = new_operations.pop()
    assert w2.authority.operations[operation_id].disposition == 'COMMITTED'
    with SessionLocal() as db:
        assert db.scalar(select(t.invalidation.c.operation_id).where(
            t.invalidation.c.operation_id == operation_id)) == operation_id
        rows = list(db.execute(select(t.registry).where(t.registry.c.entry_id == entry_id)
                               .order_by(t.registry.c.version)).mappings())
        current = db.execute(select(t.registry_current).where(
            t.registry_current.c.entry_id == entry_id)).mappings().one()
    assert [row['version'] for row in rows] == [1, 2]
    assert [decode('SyntheticCandidateCertificate', bytes(row['record'])) for row in rows] == [original, corrected]
    assert current['state'] == 'ACTIVE' and current['generation'] == 2
    assert current['digest'] == corrected.fingerprint()

    w2.reopen()
    assert w2.authority.state == 'OPEN' and w2.authority.coverage()
    assert w2.registry.candidate(w2.candidates[0], original.decision_refs[3], w2.deadline()) == (corrected, set())
    failures = []
    original_requalify = w2.preparation.requalify
    def observe_requalification(*args, **kwargs):
        try:
            return original_requalify(*args, **kwargs)
        except PortError as error:
            failures.append(error.code)
            raise

    monkeypatch.setattr(w2.preparation, 'requalify', observe_requalification)
    before = sql_state(), independent_state(w2)
    with pytest.raises(PortError):
        read(cache, w2, runtime)
    # The now-open authority is not the reason for rejection: current exact
    # candidate qualification detects the corrected immutable dependency first.
    assert failures == ['CONTEXT_CHANGED'] and w2.authority.state == 'OPEN'
    assert (sql_state(), independent_state(w2)) == before
    assert w2.witness.accepted[runtime.key.fingerprint()] == 1
    assert all((balance['calls'], balance['settled_tokens'], balance['settled_microusd'],
                balance['held_tokens'], balance['held_microusd']) == (1, 2432, 8704, 0, 0)
               for balance in balances(runtime.key))


def test_explicit_64_slot_pressure_preserves_identical_remember_without_new_calls(w2, tmp_path):
    runtime, completion, outcome = completed(w2, tmp_path)
    assert outcome.code is None and completion.display_state == 'ELIGIBLE_NOW'
    assert MAX_ENTRIES == 64

    def pressure(count):
        cache = AnalysisCache()
        # Inert storage pressure only: these are neither qualified origins nor
        # readable entries. The one real origin still passes every seal check.
        for index in range(count):
            key = 'synthetic_inert_slot_' + str(index)
            cache._entries[key] = b'{}'
            cache._seals[key] = object()
        return cache

    cache = pressure(63)
    before = sql_state(), independent_state(w2)
    key = cache.remember_v1(runtime, completion, outcome)
    assert len(cache._entries) == len(cache._seals) == 64
    contents = dict(cache._entries), dict(cache._seals)
    assert cache.remember_v1(runtime, completion, outcome) == key
    assert (cache._entries, cache._seals) == contents
    assert read(cache, w2, runtime).display == outcome.display

    full = pressure(64)
    contents = dict(full._entries), dict(full._seals)
    with pytest.raises(PortError, match='LIMIT_EXCEEDED'):
        full.remember_v1(runtime, completion, outcome)
    assert (full._entries, full._seals) == contents
    assert (sql_state(), independent_state(w2)) == before
    assert w2.witness.accepted[runtime.key.fingerprint()] == 1 and w2.authority.coverage()
